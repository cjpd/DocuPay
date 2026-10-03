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


def test_finished_document_is_skipped(org):
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.APPROVED)
    provider = FakeProvider(fast=CLEAN_INVOICE)
    _run(doc, provider)
    assert provider.calls == []
    _run(doc, provider, force=True)
    assert provider.calls == ["fast"]


def test_rerun_updates_in_place(org):
    doc = make_document(org, INVOICE_TEXT.encode())
    bad = {**CLEAN_INVOICE, "total_amount": 1.0}
    _run(doc, FakeProvider(fast=bad, strong=bad))
    _run(doc, FakeProvider(fast=bad, strong=bad), force=True)
    assert ExtractedData.objects.filter(document=doc).count() == 1
    assert ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).count() == 1


def test_missing_document():
    assert "skipped" in tasks.process_document.apply(args=[999999]).get()
