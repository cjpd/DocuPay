import json
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import openai
import pytest
from django.test import override_settings

from apps.processing.errors import ModelOutputError, PermanentProcessingError, TransientProcessingError
from apps.processing.ingest import DocumentInput, ImagePart
from apps.processing.providers import get_provider
from apps.processing.providers.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from apps.processing.providers.base import FAST, STRONG
from apps.processing.providers.openai_provider import OpenAIProvider
from apps.processing.schema import INVOICE_JSON_SCHEMA

from .factories import CLEAN_INVOICE

TEXT_DOC = DocumentInput(source="pdf_text", page_count=1, text="Invoice INV-1001")
IMAGE_DOC = DocumentInput(source="image", page_count=1, images=[ImagePart("image/png", b"\x89PNGdata")])


def _http_error(cls, status):
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.example.com"))
    return cls("error", response=resp, body=None)


class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response, self.error = response, error
        self.messages = SimpleNamespace(create=self._create("messages"))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create("beta")))

    def _create(self, kind):
        def create(**params):
            self.calls.append((kind, params))
            if self.error:
                raise self.error
            return self.response
        return create


def claude_response(data=CLEAN_INVOICE, stop_reason="end_turn", model="claude-haiku-4-5"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(data))],
        stop_reason=stop_reason, model=model,
        usage=SimpleNamespace(input_tokens=2000, output_tokens=400),
    )


def test_anthropic_fast_request_shape():
    client = FakeAnthropic(claude_response())
    result = AnthropicProvider(client).extract(IMAGE_DOC, FAST)
    kind, params = client.calls[0]
    assert kind == "messages"
    assert params["model"] == "claude-haiku-4-5"
    assert params["output_config"]["format"] == {"type": "json_schema", "schema": INVOICE_JSON_SCHEMA}
    assert "effort" not in params["output_config"]  # Haiku 4.5 does not accept effort
    blocks = params["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert result.extraction.invoice_number == "INV-1001"
    # 2000 * $1 + 400 * $5 per million tokens
    assert result.cost_usd == Decimal("0.004000")


def test_anthropic_strong_uses_effort_and_fallbacks():
    client = FakeAnthropic(claude_response(model="claude-opus-5-5"))
    result = AnthropicProvider(client).extract(TEXT_DOC, STRONG)
    kind, params = client.calls[0]
    assert kind == "beta"
    assert params["model"] == "claude-opus-5-5"
    assert params["output_config"]["effort"] == "medium"
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    assert "<document>" in params["messages"][0]["content"][0]["text"]
    assert result.cost_usd == Decimal("0.016000")


@override_settings(ANTHROPIC_REFUSAL_FALLBACKS=False)
def test_anthropic_fallbacks_can_be_disabled():
    client = FakeAnthropic(claude_response(model="claude-opus-5-5"))
    AnthropicProvider(client).extract(TEXT_DOC, STRONG)
    assert client.calls[0][0] == "messages"


@pytest.mark.parametrize("error,expected", [
    (_http_error(anthropic.RateLimitError, 429), TransientProcessingError),
    (_http_error(anthropic.InternalServerError, 500), TransientProcessingError),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), TransientProcessingError),
    (_http_error(anthropic.BadRequestError, 400), PermanentProcessingError),
    (_http_error(anthropic.AuthenticationError, 401), PermanentProcessingError),
])
def test_anthropic_error_mapping(error, expected):
    with pytest.raises(expected) as info:
        AnthropicProvider(FakeAnthropic(error=error)).extract(TEXT_DOC)
    assert not isinstance(info.value, ModelOutputError)  # request errors are not escalated
    assert "error" not in str(info.value).lower() or "HTTP" in str(info.value)  # no raw provider text


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_bad_stop_reasons(stop_reason):
    with pytest.raises(ModelOutputError):
        AnthropicProvider(FakeAnthropic(claude_response(stop_reason=stop_reason))).extract(TEXT_DOC)


def test_dated_model_id_is_priced():
    client = FakeAnthropic(claude_response(model="claude-haiku-4-5-20251001"))
    assert AnthropicProvider(client).extract(TEXT_DOC).cost_usd == Decimal("0.004000")


def test_anthropic_invalid_json():
    resp = claude_response()
    resp.content[0].text = "{not json"
    with pytest.raises(ModelOutputError, match="invalid"):
        AnthropicProvider(FakeAnthropic(resp)).extract(TEXT_DOC)


class FakeOpenAI:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return self.response


def openai_response(data=CLEAN_INVOICE, finish_reason="stop", refusal=None):
    message = SimpleNamespace(content=json.dumps(data), refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="gpt-5-mini-2026", usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
    )


@override_settings(OPENAI_PRICING={"gpt-5-mini": [0.25, 2.0]})
def test_openai_request_shape():
    client = FakeOpenAI(openai_response())
    result = OpenAIProvider(client).extract(IMAGE_DOC, FAST)
    params = client.calls[0]
    assert params["model"] == "gpt-5-mini"
    fmt = params["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert params["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.extraction.total_amount == Decimal("324.00")
    assert result.cost_usd == Decimal("0.000650")


def test_openai_refusal_and_length():
    with pytest.raises(PermanentProcessingError):
        OpenAIProvider(FakeOpenAI(openai_response(refusal="no"))).extract(TEXT_DOC)
    with pytest.raises(PermanentProcessingError):
        OpenAIProvider(FakeOpenAI(openai_response(finish_reason="length"))).extract(TEXT_DOC)


@pytest.mark.parametrize("error,expected", [
    (_http_error(openai.RateLimitError, 429), TransientProcessingError),
    (_http_error(openai.BadRequestError, 400), PermanentProcessingError),
])
def test_openai_error_mapping(error, expected):
    with pytest.raises(expected):
        OpenAIProvider(FakeOpenAI(error=error)).extract(TEXT_DOC)


@pytest.mark.parametrize("name,cls", [("anthropic", AnthropicProvider), ("openai", OpenAIProvider)])
def test_get_provider(name, cls):
    assert isinstance(get_provider(name), cls)


def test_get_provider_unknown():
    with pytest.raises(ValueError):
        get_provider("nope")


def test_missing_api_key_is_a_clear_permanent_error(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent")
    with pytest.raises(PermanentProcessingError, match="not configured"):
        AnthropicProvider().extract(TEXT_DOC)
