from unittest import mock

import pytest

from apps.documents.models import Document, ExtractedData, ReviewTask
from apps.processing import tasks
from apps.processing.errors import TransientProcessingError
from apps.processing.providers.fake import FakeProvider

from .factories import CLEAN_INVOICE, INVOICE_TEXT, make_document, make_org, text_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return make_org()[0]


def _run(doc, provider, **kwargs):
    with mock.patch.object(tasks, "get_provider", return_value=provider):
        tasks.process_document.apply(args=[doc.id], kwargs=kwargs)
    doc.refresh_from_db()
    return doc


def test_clean_invoice_is_auto_approved(org):
    doc = _run(make_document(org, text_pdf(), "inv.pdf"), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED
    assert doc.approved_at is not None
    assert doc.page_count == 1
    assert doc.processing_meta["source"] == "pdf_text"
    data = doc.extracted_data
    assert str(data.total_amount) == "324.00"
    assert data.overall_confidence == 1.0
    assert data.validation and all(c["status"] in ("pass", "skip") for c in data.validation)
    assert not ReviewTask.objects.filter(document=doc).exists()


def test_bad_invoice_goes_to_review(org):
    bad = {**CLEAN_INVOICE, "total_amount": 1.0}
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=bad, strong=bad))
    assert doc.status == Document.Status.REQUIRES_REVIEW
    assert ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).count() == 1
    assert doc.processing_meta["escalated"] is True


def test_duplicate_invoice_goes_to_review(org):
    _run(make_document(org, INVOICE_TEXT.encode(), "a.txt"), FakeProvider(fast=CLEAN_INVOICE))
    second = _run(make_document(org, INVOICE_TEXT.encode(), "b.txt"), FakeProvider(fast=CLEAN_INVOICE, strong=CLEAN_INVOICE))
    assert second.status == Document.Status.REQUIRES_REVIEW
    dup = next(c for c in second.extracted_data.validation if c["name"] == "duplicate")
    assert dup["status"] == "fail"


def test_duplicate_check_is_per_organization(org):
    other, _ = make_org(slug="other")
    _run(make_document(other, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED


def test_bad_file_marks_failed(org):
    doc = _run(make_document(org, b"%PDF broken", "x.pdf"), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.FAILED
    assert "damaged" in doc.error_message
    assert not ExtractedData.objects.filter(document=doc).exists()


def test_transient_errors_retry_then_fail(org):
    provider = FakeProvider(fast=TransientProcessingError("429"))
    doc = _run(make_document(org, INVOICE_TEXT.encode()), provider)
    assert doc.status == Document.Status.FAILED
    assert "Gave up after" in doc.error_message
    # One first try plus MAX_RETRIES retries.
    assert len(provider.calls) == tasks.MAX_RETRIES + 1


def test_transient_error_then_success(org):
    class Flaky(FakeProvider):
        def extract(self, doc, tier="fast"):
            if not self.calls:
                self.calls.append(tier)
                raise TransientProcessingError("overloaded")
            return super().extract(doc, tier)

    doc = _run(make_document(org, INVOICE_TEXT.encode()), Flaky(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED


def test_approved_document_is_never_reprocessed(org):
    """Reprocessing must not overwrite a reviewer's corrections, even with force=True."""
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.APPROVED)
    provider = FakeProvider(fast=CLEAN_INVOICE)
    _run(doc, provider)
    _run(doc, provider, force=True)
    assert provider.calls == []


def test_force_reprocesses_document_in_review(org):
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    provider = FakeProvider(fast=CLEAN_INVOICE)
    _run(doc, provider)
    assert provider.calls == []
    _run(doc, provider, force=True)
    assert provider.calls == ["fast"]


def test_document_owned_by_another_run_is_skipped(org):
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.PROCESSING)
    Document.objects.filter(id=doc.id).update(processing_task_id="other-task")
    provider = FakeProvider(fast=CLEAN_INVOICE)
    doc = _run(doc, provider)
    assert provider.calls == []
    assert doc.status == Document.Status.PROCESSING


def test_result_is_discarded_if_another_run_took_over(org):
    doc = make_document(org, INVOICE_TEXT.encode())

    class TakeOver(FakeProvider):
        def extract(self, doc_input, tier="fast"):
            Document.objects.filter(id=doc.id).update(processing_task_id="newer-run")
            return super().extract(doc_input, tier)

    doc = _run(doc, TakeOver(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.PROCESSING
    assert not ExtractedData.objects.filter(document=doc).exists()


def test_unexpected_error_marks_failed_without_details(org):
    doc = make_document(org, INVOICE_TEXT.encode())
    with mock.patch.object(tasks, "run_pipeline", side_effect=ValueError("secret internals")):
        doc = _run(doc, FakeProvider())
    assert doc.status == Document.Status.FAILED
    assert doc.error_message == tasks.GENERIC_ERROR


def test_amount_above_org_limit_goes_to_review(org):
    org.auto_approve_max_amount = 100
    org.save()
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.REQUIRES_REVIEW
    assert doc.processing_meta["escalated"] is False  # a limit is not an extraction error


def test_duplicate_does_not_pay_for_escalation(org):
    _run(make_document(org, INVOICE_TEXT.encode(), "a.txt"), FakeProvider(fast=CLEAN_INVOICE))
    variant = {**CLEAN_INVOICE, "vendor_name": "ACME Supplies, LLC", "invoice_number": "inv 1001"}
    provider = FakeProvider(fast=variant, strong=variant)
    second = _run(make_document(org, INVOICE_TEXT.encode(), "b.txt"), provider)
    assert second.status == Document.Status.REQUIRES_REVIEW
    assert provider.calls == ["fast"]


def test_duplicate_recheck_under_lock(org):
    """Two copies processed at the same time: when the second one ran its pipeline the
    first was not saved yet, so only the re-check under the lock can catch it."""
    from apps.processing.ingest import DocumentInput
    from apps.processing.pipeline import run_pipeline

    first = _run(make_document(org, INVOICE_TEXT.encode(), "a.txt"), FakeProvider(fast=CLEAN_INVOICE))
    assert first.status == Document.Status.APPROVED
    stale_result = run_pipeline(DocumentInput(source="text", page_count=1, text="x"),
                                FakeProvider(fast=CLEAN_INVOICE), threshold=0.92, is_duplicate=lambda ex: False)
    assert stale_result.decision == "auto_approve"
    with mock.patch.object(tasks, "run_pipeline", return_value=stale_result):
        second = _run(make_document(org, INVOICE_TEXT.encode(), "b.txt"), FakeProvider())
    assert second.status == Document.Status.REQUIRES_REVIEW
    assert second.processing_meta["decision"] == "review"


def test_stale_runs_are_marked_failed(org):
    from datetime import timedelta

    from django.utils import timezone

    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.PROCESSING)
    Document.objects.filter(id=doc.id).update(processing_started_at=timezone.now() - timedelta(hours=1))
    fresh = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.PROCESSING)
    Document.objects.filter(id=fresh.id).update(processing_started_at=timezone.now())
    tasks.fail_stale_documents()
    doc.refresh_from_db()
    fresh.refresh_from_db()
    assert doc.status == Document.Status.FAILED
    assert fresh.status == Document.Status.PROCESSING


@pytest.mark.parametrize("a,b", [
    (("ACME Inc.", "INV-001"), ("acme inc", "inv 001")),
    (("Acme Supplies, L.L.C.", "INV-1001"), ("ACME SUPPLIES", "1001")),
    (("Acme GmbH", "#0042"), ("Acme", "42")),
    (("Acme", "INV-OO42"), ("Acme", "INV-0042")),
])
def test_dedupe_key_variants_match(a, b):
    assert tasks.dedupe_key(1, *a) == tasks.dedupe_key(1, *b)


def test_dedupe_key_normalization():
    assert tasks.dedupe_key(1, "Acme", "1") != tasks.dedupe_key(2, "Acme", "1")
    assert tasks.dedupe_key(1, "Acme", "1001") != tasks.dedupe_key(1, "Acme", "1002")
    assert tasks.dedupe_key(1, "Acme", "A-17") != tasks.dedupe_key(1, "Beta", "A-17")
    assert tasks.dedupe_key(1, None, "1") == ""


def test_rerun_updates_in_place(org):
    doc = make_document(org, INVOICE_TEXT.encode())
    bad = {**CLEAN_INVOICE, "total_amount": 1.0}
    _run(doc, FakeProvider(fast=bad, strong=bad))
    _run(doc, FakeProvider(fast=bad, strong=bad), force=True)
    assert ExtractedData.objects.filter(document=doc).count() == 1
    assert ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).count() == 1


def test_missing_document():
    assert "skipped" in tasks.process_document.apply(args=[999999]).get()



def test_out_of_range_amount_is_saved_for_review(org):
    """On Postgres a 1e15 total would overflow numeric(14,2) and crash the save."""
    huge = {**CLEAN_INVOICE, "subtotal": 1e15, "total_amount": 1e15 + 24, "line_items": []}
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=huge))
    assert doc.status == Document.Status.REQUIRES_REVIEW
    assert doc.extracted_data.total_amount is None
    assert doc.extracted_data.raw_extraction["total_amount"].startswith("1000000000000024")



def test_new_vendor_rule(org):
    org.review_new_vendors = True
    org.save()
    first = _run(make_document(org, INVOICE_TEXT.encode(), "a.txt"), FakeProvider(fast=CLEAN_INVOICE))
    assert first.status == Document.Status.REQUIRES_REVIEW
    assert any(c["name"] == "new_vendor" and c["status"] == "fail" for c in first.extracted_data.validation)
    # A person approves the first invoice; the next one from the same vendor can auto-approve.
    Document.objects.filter(id=first.id).update(status=Document.Status.APPROVED)
    nxt = {**CLEAN_INVOICE, "vendor_name": "ACME Supplies, LLC", "invoice_number": "INV-1002"}
    second = _run(make_document(org, INVOICE_TEXT.encode(), "b.txt"), FakeProvider(fast=nxt))
    assert second.status == Document.Status.APPROVED


def test_new_vendor_rule_is_off_by_default(org):
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED


def test_slow_strong_call_keeps_fast_result_without_retry(org):
    """A soft time limit during escalation must not retry (that would pay for the fast call again)."""
    from celery.exceptions import SoftTimeLimitExceeded

    bad = {**CLEAN_INVOICE, "total_amount": 1.0}
    provider = FakeProvider(fast=bad, strong=SoftTimeLimitExceeded())
    doc = _run(make_document(org, INVOICE_TEXT.encode()), provider)
    assert provider.calls == ["fast", "strong"]
    assert doc.status == Document.Status.REQUIRES_REVIEW
    assert doc.processing_meta["escalation_error"] == "SoftTimeLimitExceeded"



def test_vendor_history_from_approved_invoices(org):
    _run(make_document(org, INVOICE_TEXT.encode(), "a.txt"), FakeProvider(fast=CLEAN_INVOICE))
    in_euros = {**CLEAN_INVOICE, "invoice_number": "INV-1002", "currency": "EUR"}
    doc = _run(make_document(org, INVOICE_TEXT.encode(), "b.txt"), FakeProvider(fast=in_euros, strong=in_euros))
    assert doc.status == Document.Status.REQUIRES_REVIEW
    check = next(c for c in doc.extracted_data.validation if c["name"] == "vendor_history")
    assert check["status"] == "fail" and "USD" in check["message"]


def test_vendor_equal_to_own_organization_goes_to_review(org):
    org.name = "Acme Supplies LLC"
    org.save()
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE, strong=CLEAN_INVOICE))
    assert doc.status == Document.Status.REQUIRES_REVIEW
