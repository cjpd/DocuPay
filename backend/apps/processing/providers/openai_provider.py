"""
OpenAI adapter: Chat Completions with a strict JSON Schema response format.

Model names and prices change often, so both come from settings
(OPENAI_FAST_MODEL, OPENAI_STRONG_MODEL, OPENAI_PRICING).
"""
import base64
import json

from django.conf import settings
from pydantic import ValidationError

from ..errors import PermanentProcessingError, TransientProcessingError
from ..ingest import DocumentInput
from ..schema import INVOICE_JSON_SCHEMA, InvoiceExtraction
from .base import FAST, STRONG, SYSTEM_PROMPT, USER_INSTRUCTION, ExtractionProvider, ProviderResult, estimate_cost


class OpenAIProvider(ExtractionProvider):
    name = "openai"

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import openai

            self._client = openai.OpenAI(max_retries=2, timeout=120.0)
        return self._client

    def model_for(self, tier: str) -> str:
        if tier == STRONG:
            return getattr(settings, "OPENAI_STRONG_MODEL", "gpt-5")
        return getattr(settings, "OPENAI_FAST_MODEL", "gpt-5-mini")

    def build_request(self, doc: DocumentInput, tier: str) -> dict:
        content = []
        for img in doc.images:
            b64 = base64.standard_b64encode(img.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{b64}"}})
        if doc.text:
            content.append({"type": "text", "text": f"<document>\n{doc.text}\n</document>"})
        content.append({"type": "text", "text": USER_INSTRUCTION})
        return {
            "model": self.model_for(tier),
            "max_completion_tokens": 16000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "invoice", "schema": INVOICE_JSON_SCHEMA, "strict": True},
            },
        }

    def extract(self, doc: DocumentInput, tier: str = FAST) -> ProviderResult:
        import openai

        params = self.build_request(doc, tier)
        try:
            response = self.client.chat.completions.create(**params)
        except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError) as exc:
            raise TransientProcessingError(f"OpenAI API unavailable: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 429):
                raise TransientProcessingError(f"OpenAI API error {exc.status_code}") from exc
            raise PermanentProcessingError(f"OpenAI rejected the request ({exc.status_code})") from exc

        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise PermanentProcessingError("OpenAI declined to process this document")
        if choice.finish_reason == "length":
            raise PermanentProcessingError("Extraction output was cut off (length)")
        try:
            extraction = InvoiceExtraction.model_validate(json.loads(choice.message.content or ""))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise PermanentProcessingError(f"OpenAI returned invalid extraction JSON: {exc}") from exc

        usage = response.usage
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=response.model,
            tier=tier,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=estimate_cost(getattr(settings, "OPENAI_PRICING", {}), params["model"], in_tok, out_tok),
        )
