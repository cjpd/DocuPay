"""
Claude adapter: vision input plus structured JSON output (`output_config.format`).

The fast tier defaults to Claude Haiku 4.5 and the strong tier to Claude Opus 5.5.
Both are configurable. The strong tier sets effort explicitly and opts into
server-side refusal fallbacks.
"""
import base64
import json

from django.conf import settings
from pydantic import ValidationError

from ..errors import PermanentProcessingError, TransientProcessingError
from ..ingest import DocumentInput
from ..schema import INVOICE_JSON_SCHEMA, InvoiceExtraction
from .base import FAST, STRONG, SYSTEM_PROMPT, USER_INSTRUCTION, ExtractionProvider, ProviderResult, estimate_cost

# $ per million tokens (input, output). Override with ANTHROPIC_PRICING in settings.
DEFAULT_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Models that accept output_config.effort and the server-side fallback beta.
_EFFORT_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(ExtractionProvider):
    name = "anthropic"

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(max_retries=2, timeout=120.0)
        return self._client

    def model_for(self, tier: str) -> str:
        if tier == STRONG:
            return getattr(settings, "ANTHROPIC_STRONG_MODEL", "claude-opus-5-5")
        return getattr(settings, "ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")

    def build_request(self, doc: DocumentInput, tier: str) -> dict:
        model = self.model_for(tier)
        content = []
        for img in doc.images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii"),
                },
            })
        if doc.text:
            content.append({"type": "text", "text": f"<document>\n{doc.text}\n</document>"})
        content.append({"type": "text", "text": USER_INSTRUCTION})

        output_config = {"format": {"type": "json_schema", "schema": INVOICE_JSON_SCHEMA}}
        params = {
            "model": model,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "output_config": output_config,
        }
        if model.startswith(_EFFORT_MODELS):
            output_config["effort"] = getattr(settings, "ANTHROPIC_STRONG_EFFORT", "medium")
            if getattr(settings, "ANTHROPIC_REFUSAL_FALLBACKS", True):
                params["betas"] = [FALLBACK_BETA]
                params["fallbacks"] = "default"
        return params

    def extract(self, doc: DocumentInput, tier: str = FAST) -> ProviderResult:
        import anthropic

        params = self.build_request(doc, tier)
        try:
            if "betas" in params:
                response = self.client.beta.messages.create(**params)
            else:
                response = self.client.messages.create(**params)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as exc:
            raise TransientProcessingError(f"Claude API unavailable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 429):
                raise TransientProcessingError(f"Claude API error {exc.status_code}") from exc
            raise PermanentProcessingError(f"Claude API rejected the request ({exc.status_code}): {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise PermanentProcessingError("Claude declined to process this document")
        if response.stop_reason == "max_tokens":
            raise PermanentProcessingError("Extraction output was cut off (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise PermanentProcessingError("Claude returned no extraction")
        try:
            extraction = InvoiceExtraction.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise PermanentProcessingError(f"Claude returned invalid extraction JSON: {exc}") from exc

        pricing = {**DEFAULT_PRICING, **getattr(settings, "ANTHROPIC_PRICING", {})}
        usage = response.usage
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=response.model,
            tier=tier,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost(pricing, response.model, usage.input_tokens, usage.output_tokens),
        )
