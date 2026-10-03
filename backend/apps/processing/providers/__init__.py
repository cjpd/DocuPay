from django.conf import settings

from .base import FAST, STRONG, ExtractionProvider, ProviderResult


def get_provider(name: str = None) -> ExtractionProvider:
    """Select the extraction backend with settings.EXTRACTION_PROVIDER."""
    name = (name or getattr(settings, "EXTRACTION_PROVIDER", "heuristic")).lower()
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider()
    if name == "heuristic":
        from .heuristic import HeuristicProvider

        return HeuristicProvider()
    raise ValueError(f"Unknown EXTRACTION_PROVIDER: {name!r} (use anthropic, openai or heuristic)")


__all__ = ["FAST", "STRONG", "ExtractionProvider", "ProviderResult", "get_provider"]
