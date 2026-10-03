from ..ingest import DocumentInput
from ..schema import InvoiceExtraction
from .base import FAST, ExtractionProvider, ProviderResult


class FakeProvider(ExtractionProvider):
    """Test double. Returns queued extractions per tier, or raises queued errors."""

    name = "fake"

    def __init__(self, fast=None, strong=None):
        self.responses = {"fast": fast, "strong": strong}
        self.calls = []

    def extract(self, doc: DocumentInput, tier: str = FAST) -> ProviderResult:
        self.calls.append(tier)
        value = self.responses.get(tier)
        if isinstance(value, Exception):
            raise value
        if value is None:
            value = {}
        extraction = value if isinstance(value, InvoiceExtraction) else InvoiceExtraction.model_validate(value)
        return ProviderResult(extraction=extraction, provider=self.name, model=f"fake-{tier}", tier=tier,
                              input_tokens=100, output_tokens=50)
