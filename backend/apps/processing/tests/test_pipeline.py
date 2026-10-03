from decimal import Decimal

from apps.processing.errors import PermanentProcessingError
from apps.processing.ingest import DocumentInput
from apps.processing.pipeline import AUTO_APPROVE, REVIEW, run_pipeline
from apps.processing.providers.fake import FakeProvider
from apps.processing.providers.heuristic import HeuristicProvider

from .factories import CLEAN_INVOICE, INVOICE_TEXT

DOC = DocumentInput(source="text", page_count=1, text="x")
BAD = {**CLEAN_INVOICE, "total_amount": 999.0}


def test_clean_fast_result_does_not_escalate():
    provider = FakeProvider(fast=CLEAN_INVOICE, strong=CLEAN_INVOICE)
    result = run_pipeline(DOC, provider, threshold=0.92)
    assert result.decision == AUTO_APPROVE
    assert provider.calls == ["fast"]


def test_failed_checks_escalate_to_strong_model():
    provider = FakeProvider(fast=BAD, strong=CLEAN_INVOICE)
    result = run_pipeline(DOC, provider, threshold=0.92)
    assert provider.calls == ["fast", "strong"]
    assert result.decision == AUTO_APPROVE
    assert result.extraction.total_amount == Decimal("324.00")
    meta = result.meta(DOC)
    assert meta["escalated"] is True
    assert [a["tier"] for a in meta["attempts"]] == ["fast", "strong"]
    assert meta["attempts"][0]["failed_checks"] == ["totals_math"]


def test_both_fail_goes_to_review_with_best_attempt():
    worse = {**BAD, "invoice_number": None}
    provider = FakeProvider(fast=worse, strong=BAD)
    result = run_pipeline(DOC, provider, threshold=0.92)
    assert result.decision == REVIEW
    assert result.extraction.invoice_number == "INV-1001"


def test_escalation_can_be_disabled():
    provider = FakeProvider(fast=BAD)
    result = run_pipeline(DOC, provider, threshold=0.92, escalate=False)
    assert provider.calls == ["fast"]
    assert result.decision == REVIEW


def test_failed_escalation_keeps_fast_result():
    provider = FakeProvider(fast=BAD, strong=PermanentProcessingError("refused"))
    result = run_pipeline(DOC, provider, threshold=0.92)
    assert result.decision == REVIEW
    assert result.escalation_error == "refused"
    assert len(result.attempts) == 1


def test_duplicate_goes_to_review():
    result = run_pipeline(DOC, FakeProvider(fast=CLEAN_INVOICE, strong=CLEAN_INVOICE), 0.92, is_duplicate=lambda ex: True)
    assert result.decision == REVIEW


def test_cost_is_summed():
    result = run_pipeline(DOC, FakeProvider(fast=CLEAN_INVOICE), 0.92)
    assert result.total_cost is None  # fake provider has no price


def test_heuristic_provider_offline():
    doc = DocumentInput(source="text", page_count=1, text=INVOICE_TEXT)
    result = run_pipeline(doc, HeuristicProvider(), threshold=0.92)
    ex = result.extraction
    assert ex.invoice_number == "INV-1001"
    assert ex.total_amount == Decimal("324.00")
    assert ex.subtotal == Decimal("300.00")
    assert len(ex.line_items) == 2
    assert result.total_cost == Decimal("0")
    assert len(result.attempts) == 1  # no escalation offline
