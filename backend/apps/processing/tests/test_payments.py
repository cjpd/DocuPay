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


def test_unknown_vendor_with_a_bank_account_needs_a_person():
    """A look-alike vendor name ("Acme Suppl1es") with the fraudster's IBAN must not auto-approve."""
    report = validate(_ex(vendor_name="Acme Suppl1es", bank_account=FRAUD), today=TODAY, vendor_lookup=lambda e: None)
    assert _check(report).status == FAIL and _check(report).severity == "critical"
    assert not report.can_auto_approve(0.0)


def test_vendor_without_confirmed_account_needs_a_person():
    no_account = VendorRecord(id=1, name="Acme Supplies LLC")
    report = validate(_ex(bank_account=ON_FILE), today=TODAY, vendor_lookup=lambda e: no_account)
    assert _check(report).status == FAIL and "no confirmed bank account" in _check(report).message


def test_changed_bank_code_needs_a_person():
    """Same account number, different routing or sort code: the money goes to another bank."""
    vendor = VendorRecord(id=1, name="Acme", bank_account="12345678", bank_code="021000021")
    report = validate(_ex(bank_account="12345678", bank_code="999999"), today=TODAY, vendor_lookup=lambda e: vendor)
    assert _check(report).status == FAIL and "bank code" in _check(report).message
    ok = validate(_ex(bank_account="12345678", bank_code="021000021"), today=TODAY, vendor_lookup=lambda e: vendor)
    assert _check(ok).status == PASS


@pytest.mark.parametrize("due,expected", [("99999.00", FAIL), ("0", FAIL), ("-5", FAIL), ("324.00", PASS), ("100.00", PASS)])
def test_amount_due_is_checked(due, expected):
    report = validate(_ex(amount_due=due), today=TODAY)
    assert _check(report, "amount_due").status == expected
    if expected == FAIL:
        assert not report.can_auto_approve(0.0)


def test_amount_due_counts_against_the_limit():
    from decimal import Decimal

    report = validate(_ex(amount_due="324.00"), today=TODAY, max_amount=Decimal("300"))
    assert _check(report, "amount_limit").status == FAIL


@pytest.fixture
def org():
    return make_org()[0]


def test_approval_only_proposes_an_account_an_admin_confirms(org):
    """Critic finding: a member (or the first invoice) must never set where a vendor is paid."""
    owner = org.memberships.first()
    owner.role = "owner"
    owner.save()
    doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    data = ExtractedData.objects.create(document=doc, vendor_name="Acme Supplies LLC", bank_account=ON_FILE, bank_code="COBADEFFXXX")
    vendor = learn_from_approval(data)
    assert vendor.bank_account == ""  # nothing on file yet
    assert vendor.proposed_bank_account == ON_FILE and vendor.proposed_bank_document_id == doc.id
    # A second invoice with another account does not replace the proposal.
    doc2 = make_document(org, b"y", status=Document.Status.REQUIRES_REVIEW)
    learn_from_approval(ExtractedData.objects.create(document=doc2, vendor_name="Acme Supplies LLC", bank_account=FRAUD))
    vendor.refresh_from_db()
    assert vendor.proposed_bank_account == ON_FILE
    client = APIClient()
    client.force_authenticate(owner.user)
    resp = client.post(f"/api/documents/vendors/{vendor.id}/bank-account/", {"decision": "confirm"}, format="json")
    assert resp.status_code == 200 and resp.data["bank_account"] == ON_FILE and resp.data["proposed_bank_account"] == ""


def test_member_cannot_confirm_a_bank_account(org):
    from apps.organizations.models import OrgMembership

    vendor = Vendor.objects.create(organization=org, name="Acme", proposed_bank_account=FRAUD)
    member = make_org(slug="m")[1]
    OrgMembership.objects.create(organization=org, user=member, role="member")
    client = APIClient()
    client.force_authenticate(member)
    client.credentials(HTTP_X_ORGANIZATION_ID=str(org.id))
    assert client.post(f"/api/documents/vendors/{vendor.id}/bank-account/", {"decision": "confirm"}, format="json").status_code == 403
    assert client.patch(f"/api/documents/vendors/{vendor.id}/", {"bank_account": FRAUD}, format="json").status_code == 403
    vendor.refresh_from_db()
    assert vendor.bank_account == ""


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


def _task_with(org, **fields):
    doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    base = {"vendor_name": "Acme Supplies LLC", "invoice_number": "INV-5", "invoice_date": "2026-09-01",
            "total_amount": "324.00", "currency": "USD"}
    ExtractedData.objects.create(document=doc, **{**base, **fields})
    return ReviewTask.objects.create(document=doc)


@pytest.fixture
def people(org):
    from apps.organizations.models import OrgMembership

    owner_user = org.memberships.first().user
    org.memberships.update(role="owner")
    member_user = make_org(slug="m")[1]
    OrgMembership.objects.create(organization=org, user=member_user, role="member")
    owner, member = APIClient(), APIClient()
    owner.force_authenticate(owner_user)
    member.force_authenticate(member_user)
    member.credentials(HTTP_X_ORGANIZATION_ID=str(org.id))
    return owner, member


def test_member_cannot_approve_a_bank_account_change(org, people):
    """Critic finding 1: corrections were not re-checked, so a member could redirect a payment."""
    owner, member = people
    Vendor.objects.create(organization=org, name="Acme Supplies LLC", bank_account=ON_FILE)
    task = _task_with(org, bank_account=ON_FILE)
    url = f"/api/documents/reviews/{task.id}/approve/"
    resp = member.post(url, {"corrections": {"bank_account": FRAUD}, "confirm_fraud_checks": True}, format="json")
    assert resp.status_code == 403
    task.refresh_from_db()
    assert task.status == ReviewTask.STATUS_PENDING
    assert ExtractedData.objects.get(document=task.document).bank_account == ON_FILE  # nothing saved


def test_owner_must_confirm_explicitly(org, people):
    owner, _ = people
    Vendor.objects.create(organization=org, name="Acme Supplies LLC", bank_account=ON_FILE)
    task = _task_with(org, bank_account=FRAUD)
    url = f"/api/documents/reviews/{task.id}/approve/"
    resp = owner.post(url, {"corrections": {}}, format="json")
    assert resp.status_code == 409 and resp.data["requires_confirmation"] is True
    assert resp.data["checks"][0]["name"] == "bank_account"
    resp = owner.post(url, {"corrections": {}, "confirm_fraud_checks": True}, format="json")
    assert resp.status_code == 200
    vendor = Vendor.objects.get()
    assert vendor.bank_account == ON_FILE  # approving never changes where the vendor is paid


def test_corrected_number_that_duplicates_is_caught(org, people):
    """Critic finding 1: a corrected invoice number colliding with an approved one skipped the duplicate check."""
    owner, member = people
    first = _task_with(org, invoice_number="INV-1")
    assert owner.post(f"/api/documents/reviews/{first.id}/approve/", {"corrections": {}}, format="json").status_code == 200
    second = _task_with(org, invoice_number="INV-2")
    resp = member.post(f"/api/documents/reviews/{second.id}/approve/", {"corrections": {"invoice_number": "INV-1"}}, format="json")
    assert resp.status_code == 403 and resp.data["checks"][0]["name"] == "duplicate"


def test_normal_corrections_still_work_for_members(org, people):
    _, member = people
    task = _task_with(org)
    resp = member.post(f"/api/documents/reviews/{task.id}/approve/", {"corrections": {"total_amount": "300.00"}}, format="json")
    assert resp.status_code == 200
    data = ExtractedData.objects.get(document=task.document)
    assert str(data.total_amount) == "300.00" and data.validation  # re-validated and stored


def test_second_decision_is_refused(org, people):
    owner, _ = people
    task = _task_with(org)
    url = f"/api/documents/reviews/{task.id}/approve/"
    assert owner.post(url, {"corrections": {}}, format="json").status_code == 200
    assert owner.post(url, {"corrections": {}}, format="json").status_code == 409
    assert owner.post(f"/api/documents/reviews/{task.id}/reject/").status_code == 409


def test_reviewer_correction_is_normalized_and_validated(org, people):
    owner, _ = people
    task = _task_with(org)
    url = f"/api/documents/reviews/{task.id}/approve/"
    assert owner.post(url, {"corrections": {"bank_account": "DE89 3704 0044 0532 0130 01"}}, format="json").status_code == 400
    body = {"corrections": {"bank_account": "DE89 3704 0044 0532 0130 00"}, "confirm_fraud_checks": True}
    assert owner.post(url, body, format="json").status_code == 200
    assert ExtractedData.objects.get(document=task.document).bank_account == ON_FILE


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
