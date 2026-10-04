"""Payment fields and the bank account change alert (DP-30)."""
from datetime import date
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.documents.models import Document, ExtractedData, ReviewTask, Vendor
from apps.documents.vendors import learn_from_approval
from apps.processing import tasks
from apps.processing.normalize import iban_is_valid, mask_account
from apps.processing.providers.fake import FakeProvider
from apps.processing.providers.heuristic import extract_from_text
from apps.processing.schema import InvoiceExtraction
from apps.processing.validation import FAIL, PASS, SKIP, VendorRecord, validate

from .factories import CLEAN_INVOICE, INVOICE_TEXT, make_document, make_org

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 3)
ON_FILE = "DE89370400440532013000"
FRAUD = "GB33BUKB20201555555555"  # valid IBAN, different bank


def _ex(**kw):
    return InvoiceExtraction.model_validate({**CLEAN_INVOICE, **kw})


def _check(report, name="bank_account"):
    return next(c for c in report.checks if c.name == name)


VENDOR = VendorRecord(id=1, name="Acme Supplies LLC", bank_account=ON_FILE)


def test_iban_checksum():
    assert iban_is_valid(ON_FILE) and iban_is_valid(FRAUD)
    assert not iban_is_valid("DE89370400440532013001")
    assert mask_account(ON_FILE) == "…3000"


def test_bank_account_is_normalized():
    assert _ex(bank_account="de89 3704 0044 0532 0130 00").bank_account == ON_FILE


def test_same_account_passes():
    report = validate(_ex(bank_account=ON_FILE), today=TODAY, vendor_lookup=lambda e: VENDOR)
    assert _check(report).status == PASS
    assert report.can_auto_approve(0.92)


def test_changed_account_always_needs_a_person():
    report = validate(_ex(bank_account=FRAUD), today=TODAY, vendor_lookup=lambda e: VENDOR)
    check = _check(report)
    assert check.status == FAIL and check.severity == "critical"
    assert "…5555" in check.message and "…3000" in check.message and "phone" in check.message
    assert FRAUD not in check.message  # masked in messages
    assert not report.can_auto_approve(0.0)


def test_misread_iban_is_flagged_and_escalated():
    report = validate(_ex(bank_account="DE89370400440532013001"), today=TODAY)
    assert _check(report).status == FAIL
    assert report.worth_escalating


def test_no_account_on_invoice_is_skipped():
    assert _check(validate(_ex(), today=TODAY, vendor_lookup=lambda e: VENDOR)).status == SKIP


def test_unknown_vendor_with_valid_account_passes():
    assert _check(validate(_ex(bank_account=FRAUD), today=TODAY)).status == PASS


@pytest.fixture
def org():
    return make_org()[0]


def test_first_account_is_learned_but_never_replaced(org):
    doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    data = ExtractedData.objects.create(document=doc, vendor_name="Acme Supplies LLC", bank_account=ON_FILE, bank_code="COBADEFFXXX")
    vendor = learn_from_approval(data)
    assert vendor.bank_account == ON_FILE and vendor.bank_code == "COBADEFFXXX"
    doc2 = make_document(org, b"y", status=Document.Status.REQUIRES_REVIEW)
    data2 = ExtractedData.objects.create(document=doc2, vendor_name="Acme Supplies LLC", bank_account=FRAUD)
    learn_from_approval(data2)
    vendor.refresh_from_db()
    assert vendor.bank_account == ON_FILE  # an invoice can never change where we pay


def test_pipeline_sends_changed_account_to_review(org):
    Vendor.objects.create(organization=org, name="Acme Supplies LLC", bank_account=ON_FILE)
    fraud = {**CLEAN_INVOICE, "bank_account": FRAUD}
    with mock.patch.object(tasks, "get_provider", return_value=FakeProvider(fast=fraud, strong=fraud)):
        doc = make_document(org, INVOICE_TEXT.encode())
        tasks.process_document.apply(args=[doc.id])
    doc.refresh_from_db()
    assert doc.status == Document.Status.REQUIRES_REVIEW
    assert doc.extracted_data.bank_account == FRAUD
    assert any(c["name"] == "bank_account" and c["status"] == "fail" for c in doc.extracted_data.validation)


def test_only_admins_change_the_account_and_it_must_be_valid(org):
    owner = org.memberships.first()
    owner.role = "owner"
    owner.save()
    vendor = Vendor.objects.create(organization=org, name="Acme Supplies LLC", bank_account=ON_FILE)
    client = APIClient()
    client.force_authenticate(owner.user)
    url = f"/api/documents/vendors/{vendor.id}/"
    assert client.patch(url, {"bank_account": "DE89370400440532013001"}, format="json").status_code == 400
    resp = client.patch(url, {"bank_account": "gb33 bukb 2020 1555 5555 55"}, format="json")
    assert resp.status_code == 200 and resp.data["bank_account"] == FRAUD


def test_reviewer_correction_is_normalized_and_validated(org):
    client = APIClient()
    client.force_authenticate(org.memberships.first().user)
    doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    ExtractedData.objects.create(document=doc, vendor_name="Acme", invoice_number="1", invoice_date="2026-09-01",
                                 total_amount="10.00")
    task = ReviewTask.objects.create(document=doc)
    url = f"/api/documents/reviews/{task.id}/approve/"
    assert client.post(url, {"corrections": {"bank_account": "DE89 3704 0044 0532 0130 01"}}, format="json").status_code == 400
    assert client.post(url, {"corrections": {"bank_account": "DE89 3704 0044 0532 0130 00"}}, format="json").status_code == 200
    doc.extracted_data.refresh_from_db()
    assert doc.extracted_data.bank_account == ON_FILE


def test_export_includes_payment_fields(org):
    client = APIClient()
    client.force_authenticate(org.memberships.first().user)
    doc = make_document(org, b"x", status=Document.Status.APPROVED)
    ExtractedData.objects.create(document=doc, vendor_name="Acme", total_amount="10.00", amount_due="4.00",
                                 bank_account=ON_FILE, bank_code="COBADEFFXXX")
    body = b"".join(client.get("/api/documents/export/").streaming_content).decode()
    assert "Amount due,Bank account,Bank code" in body
    assert f"4.00,{ON_FILE},COBADEFFXXX" in body


def test_heuristic_reads_iban_and_amount_due():
    ex = extract_from_text(INVOICE_TEXT + "IBAN: DE89 3704 0044 0532 0130 00\nBIC: COBADEFFXXX\nAmount due: 124.00\n")
    assert ex.bank_account == ON_FILE and ex.bank_code == "COBADEFFXXX"
    assert str(ex.amount_due) == "124.00"


def test_bank_lines_are_not_line_items():
    ex = extract_from_text(INVOICE_TEXT + "IBAN: GB33 BUKB 2020 1555 5555 55\n")
    assert len(ex.line_items) == 2
