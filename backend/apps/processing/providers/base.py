from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..ingest import DocumentInput
from ..schema import InvoiceExtraction

FAST = "fast"
STRONG = "strong"

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "invoice_system.txt").read_text()

USER_INSTRUCTION = "Extract the invoice data from this document."


@dataclass
class ProviderResult:
    extraction: InvoiceExtraction
    provider: str
    model: str
    tier: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Optional[Decimal] = None

    def meta(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "tier": self.tier,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": str(self.cost_usd) if self.cost_usd is not None else None,
        }


class ExtractionProvider:
    """Interface for invoice extraction backends."""

    name = "base"
    # False when a second, stronger attempt cannot do better (offline providers).
    supports_escalation = True

    def extract(self, doc: DocumentInput, tier: str = FAST) -> ProviderResult:
        raise NotImplementedError


def estimate_cost(pricing: dict, model: str, input_tokens: int, output_tokens: int) -> Optional[Decimal]:
    """pricing maps model id -> (input $/MTok, output $/MTok). The API may report a
    dated id (claude-haiku-4-5-20251001), so the longest matching prefix wins."""
    rates = pricing.get(model)
    if rates is None:
        prefixes = [key for key in pricing if model.startswith(key)]
        rates = pricing[max(prefixes, key=len)] if prefixes else None
    if not rates:
        return None
    in_rate, out_rate = (Decimal(str(r)) for r in rates)
    cost = (Decimal(input_tokens) * in_rate + Decimal(output_tokens) * out_rate) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))
