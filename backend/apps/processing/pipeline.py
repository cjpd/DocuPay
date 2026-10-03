"""
Document pipeline: ingest -> fast model -> validate -> (strong model -> validate) -> decision.

The strong model runs only when the fast result cannot auto-approve, so most
clean invoices cost one cheap call. Every attempt is recorded with its model,
tokens and estimated cost.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, List, Optional

from django.conf import settings

from .errors import ModelOutputError, ProcessingError
from .ingest import DocumentInput
from .providers import FAST, STRONG, ExtractionProvider, ProviderResult
from .schema import InvoiceExtraction
from .validation import ValidationReport, validate

AUTO_APPROVE = "auto_approve"
REVIEW = "review"


@dataclass
class Attempt:
    result: ProviderResult
    report: ValidationReport

    def meta(self) -> dict:
        return self.result.meta() | {
            "score": self.report.score,
            "amounts_proved": self.report.amounts_proved,
            "failed_checks": [c.name for c in self.report.failures],
        }


@dataclass
class PipelineResult:
    extraction: InvoiceExtraction
    report: ValidationReport
    decision: str
    attempts: List[Attempt] = field(default_factory=list)
    escalation_error: Optional[str] = None

    @property
    def total_cost(self) -> Optional[Decimal]:
        costs = [a.result.cost_usd for a in self.attempts]
        if any(c is None for c in costs):
            return None
        return sum(costs, Decimal("0"))

    def meta(self, doc: DocumentInput) -> dict:
        cost = self.total_cost
        return {
            "source": doc.source,
            "page_count": doc.page_count,
            "decision": self.decision,
            "escalated": len(self.attempts) > 1,
            "attempts": [a.meta() for a in self.attempts],
            "escalation_error": self.escalation_error,
            "total_cost_usd": str(cost) if cost is not None else None,
        }


def run_pipeline(
    doc: DocumentInput,
    provider: ExtractionProvider,
    threshold: float,
    is_duplicate: Optional[Callable[[InvoiceExtraction], bool]] = None,
    escalate: Optional[bool] = None,
    max_amount: Optional[Decimal] = None,
) -> PipelineResult:
    if escalate is None:
        escalate = getattr(settings, "EXTRACTION_ESCALATE", True)
    can_escalate = escalate and provider.supports_escalation

    def check(result: ProviderResult) -> Attempt:
        return Attempt(result, validate(result.extraction, is_duplicate=is_duplicate, max_amount=max_amount))

    attempts = []
    escalation_error = None
    try:
        attempts.append(check(provider.extract(doc, FAST)))
    except ModelOutputError as exc:
        # The fast model's answer was unusable (refusal, invalid JSON, cut off). The strong model may still succeed.
        if not can_escalate:
            raise
        escalation_error = f"fast model: {exc}"

    if not attempts:
        attempts.append(check(provider.extract(doc, STRONG)))
    elif can_escalate and not attempts[0].report.can_auto_approve(threshold) and attempts[0].report.worth_escalating:
        # A failed escalation must not throw away a usable fast result: keep it and send it to review.
        try:
            attempts.append(check(provider.extract(doc, STRONG)))
        except ProcessingError as exc:
            escalation_error = str(exc)

    # Prefer the attempt that can auto-approve; else the highest score; ties go to the later (stronger) one.
    best = max(
        enumerate(attempts),
        key=lambda pair: (pair[1].report.can_auto_approve(threshold), pair[1].report.score, pair[0]),
    )[1]
    decision = AUTO_APPROVE if best.report.can_auto_approve(threshold) else REVIEW
    return PipelineResult(extraction=best.result.extraction, report=best.report, decision=decision,
                          attempts=attempts, escalation_error=escalation_error)
