"""Vendor list (DP-22): matching, learning from approvals, checks, API."""
from datetime import date
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.documents.models import Document, ExtractedData, ReviewTask, Vendor
from apps.documents.vendors import learn_from_approval, match_vendor
from apps.organizations.models import OrgMembership
from apps.processing import tasks
from apps.processing.providers.fake import FakeProvider
from apps.processing.schema import InvoiceExtraction
from apps.processing.validation import FAIL, PASS, SKIP, VendorRecord, validate

from .factories import CLEAN_INVOICE, INVOICE_TEXT, make_document, make_org

pytestmark = pytest.mark.django_db
TODAY = date(2026, 10, 3)


@pytest.fixture
def org():
    return make_org()[0]


def _status(report, name):
    return next(c for c in report.checks if c.name == name).status


# --- matching -----------------------------------------------------------------

def test_match_by_tax_id_then_name_then_alias(org):
    v = Vendor.objects.create(organization=org, name="Acme Supplies LLC", tax_id="DE 811 234 567", aliases=["ACME Wholesale"])
    assert v.tax_id == "DE811234567" and v.name_key == "acmesupplies"
    assert match_vendor(org.id, "Totally Different Name", "de811234567") == v
    assert match_vendor(org.id, "ACME Supplies, L.L.C.") == v
    assert match_vendor(org.id, "Acme Wholesale Inc.") == v
    assert match_vendor(org.id, "Beta Corp") is None


def test_matching_is_per_organization(org):
    other, _ = make_org(slug="other")
    Vendor.objects.create(organization=other, name="Acme", tax_id="X1")
    assert match_vendor(org.id, "Acme", "X1") is None


# --- checks -------------------------------------------------------------------

def _ex(**kw):
    return InvoiceExtraction.model_validate({**CLEAN_INVOICE, "vendor_tax_id": "DE811234567", **kw})


KNOWN = VendorRecord(id=1, name="Acme Supplies LLC", tax_id="DE811234567", default_currency="USD")


def test_known_vendor_passes():
    report = validate(_ex(), today=TODAY, vendor_lookup=lambda e: KNOWN)
    assert _status(report, "vendor_master") == PASS
    assert report.can_auto_approve(0.92)


def test_unknown_vendor_is_skipped_not_blocked():
    assert _status(validate(_ex(), today=TODAY, vendor_lookup=lambda e: None), "vendor_master") == SKIP


def test_tax_id_mismatch_is_critical():
    """A different tax ID on a known vendor's invoice: a classic fraud signal, or a misread."""
    report = validate(_ex(vendor_tax_id="DE999999999"), today=TODAY, vendor_lookup=lambda e: KNOWN)
    check = next(c for c in report.checks if c.name == "vendor_master")
    assert check.status == FAIL and check.severity == "critical"
    assert "DE999999999" in check.message
    assert not report.can_auto_approve(0.0)
    assert report.worth_escalating  # a stronger model may read it correctly


def test_blocked_vendor_needs_a_person():
    blocked = VendorRecord(id=1, name="Acme", is_blocked=True)
    assert not validate(_ex(), today=TODAY, vendor_lookup=lambda e: blocked).can_auto_approve(0.0)


def test_currency_differs_from_vendor_default():
    report = validate(_ex(currency="EUR"), today=TODAY, vendor_lookup=lambda e: KNOWN)
    assert _status(report, "vendor_master") == FAIL


# --- learning from approvals ----------------------------------------------------

def _review(org, user=None, **fields):
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    data = ExtractedData.objects.create(document=doc, **{"vendor_name": "Acme Supplies LLC", "currency": "USD", **fields})
    return doc, data, ReviewTask.objects.create(document=doc)


def test_approval_adds_vendor(org):
    doc, data, task = _review(org, vendor_tax_id="DE811234567")
    vendor = learn_from_approval(data)
    assert vendor.name == "Acme Supplies LLC" and vendor.tax_id == "DE811234567" and vendor.default_currency == "USD"
    data.refresh_from_db()
    assert data.vendor_id == vendor.id


def test_approval_fills_gaps_but_never_overwrites_or_adds_names(org):
    vendor = Vendor.objects.create(organization=org, name="Acme Supplies LLC", tax_id="DE811234567")
    # Matched by tax ID with a different name: possibly an impostor, so the name is not learned.
    _, data, _ = _review(org, vendor_name="Totally Other Name", vendor_tax_id="DE811234567", currency="EUR")
    learn_from_approval(data)
    vendor.refresh_from_db()
    assert vendor.aliases == []
    assert vendor.default_currency == "EUR"  # was empty
    _, data2, _ = _review(org, vendor_name="Acme Supplies LLC", vendor_tax_id="", currency="USD")
    learn_from_approval(data2)
    vendor.refresh_from_db()
    assert vendor.tax_id == "DE811234567" and vendor.default_currency == "EUR"  # unchanged


def test_approve_endpoint_learns_vendor_and_updates_duplicate_key(org):
    user = org.memberships.first().user
    client = APIClient()
    client.force_authenticate(user)
    doc, data, task = _review(org, invoice_number="WRONG-1", total_amount="324.00")
    resp = client.post(f"/api/documents/reviews/{task.id}/approve/",
                       {"corrections": {"invoice_number": "INV-1001", "vendor_tax_id": "DE811234567",
                                        "invoice_date": "2026-09-01"}}, format="json")
    assert resp.status_code == 200
    data.refresh_from_db()
    assert data.vendor and data.vendor.tax_id == "DE811234567"
    assert data.dedupe_key == tasks.dedupe_key(org.id, "Acme Supplies LLC", "INV-1001")
    # The corrected number is now found as a duplicate.
    dup = {**CLEAN_INVOICE, "vendor_name": "Acme Supplies LLC"}
    with mock.patch.object(tasks, "get_provider", return_value=FakeProvider(fast=dup, strong=dup)):
        tasks.process_document.apply(args=[make_document(org, INVOICE_TEXT.encode()).id])
    newest = Document.objects.order_by("-id").first()
    assert newest.status == Document.Status.REQUIRES_REVIEW
    assert any(c["name"] == "duplicate" and c["status"] == "fail" for c in newest.extracted_data.validation)


# --- pipeline ------------------------------------------------------------------

def _run(doc, provider):
    with mock.patch.object(tasks, "get_provider", return_value=provider):
        tasks.process_document.apply(args=[doc.id])
    doc.refresh_from_db()
    return doc


def test_processing_links_vendor(org):
    vendor = Vendor.objects.create(organization=org, name="Acme Supplies LLC", default_currency="USD")
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED
    assert doc.extracted_data.vendor_id == vendor.id


def test_blocked_vendor_goes_to_review(org):
    Vendor.objects.create(organization=org, name="Acme Supplies LLC", is_blocked=True)
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE, strong=CLEAN_INVOICE))
    assert doc.status == Document.Status.REQUIRES_REVIEW


def test_new_vendor_rule_knows_the_vendor_list(org):
    org.review_new_vendors = True
    org.save()
    Vendor.objects.create(organization=org, name="Acme Supplies LLC")
    doc = _run(make_document(org, INVOICE_TEXT.encode()), FakeProvider(fast=CLEAN_INVOICE))
    assert doc.status == Document.Status.APPROVED


# --- API -----------------------------------------------------------------------

@pytest.fixture
def api(org):
    owner = org.memberships.first()
    owner.role = "owner"
    owner.save()
    member = make_org(slug="m")[1]
    OrgMembership.objects.create(organization=org, user=member, role="member")
    a, m = APIClient(), APIClient()
    a.force_authenticate(owner.user)
    m.force_authenticate(member)
    m.credentials(HTTP_X_ORGANIZATION_ID=str(org.id))  # the member is in two companies
    return a, m


def test_list_with_invoice_counts(org, api):
    admin, member = api
    vendor = Vendor.objects.create(organization=org, name="Acme Supplies LLC")
    _, data, _ = _review(org)
    data.vendor = vendor
    data.save()
    rows = member.get("/api/documents/vendors/").data["results"]
    assert rows[0]["name"] == "Acme Supplies LLC" and rows[0]["invoice_count"] == 1


def test_only_admins_change_vendors(org, api):
    admin, member = api
    body = {"name": "Beta Corp", "tax_id": "fr 123", "default_currency": "eur"}
    assert member.post("/api/documents/vendors/", body, format="json").status_code == 403
    resp = admin.post("/api/documents/vendors/", body, format="json")
    assert resp.status_code == 201
    assert resp.data["tax_id"] == "FR123" and resp.data["default_currency"] == "EUR"
    vid = resp.data["id"]
    assert member.patch(f"/api/documents/vendors/{vid}/", {"is_blocked": True}, format="json").status_code == 403
    assert admin.patch(f"/api/documents/vendors/{vid}/", {"is_blocked": True}, format="json").data["is_blocked"] is True


@pytest.mark.parametrize("body", [
    {"name": "Acme Supplies, Inc."},  # same vendor as an existing one
    {"name": "Gamma", "default_currency": "XYZ"},
    {"name": "  "},
    {"name": "Delta", "aliases": "not a list"},
])
def test_vendor_validation(org, api, body):
    Vendor.objects.create(organization=org, name="Acme Supplies LLC")
    assert api[0].post("/api/documents/vendors/", body, format="json").status_code == 400


def test_vendors_are_tenant_scoped(org, api):
    other, _ = make_org(slug="other")
    foreign = Vendor.objects.create(organization=other, name="Secret Vendor")
    admin, _ = api
    assert admin.get(f"/api/documents/vendors/{foreign.id}/").status_code == 404
    assert admin.patch(f"/api/documents/vendors/{foreign.id}/", {"is_blocked": True}, format="json").status_code == 404
    assert admin.get("/api/documents/vendors/").data["count"] == 0


def test_cannot_approve_without_the_essentials(org):
    user = org.memberships.first().user
    client = APIClient()
    client.force_authenticate(user)
    doc, data, task = _review(org, invoice_number="INV-7")  # no date, no total
    url = f"/api/documents/reviews/{task.id}/approve/"
    resp = client.post(url, {"corrections": {}}, format="json")
    assert resp.status_code == 400
    assert resp.data["detail"] == "Fill in the invoice date, total before approving."
    task.refresh_from_db()
    assert task.status == ReviewTask.STATUS_PENDING
    resp = client.post(url, {"corrections": {"invoice_date": "2026-09-01", "total_amount": "10.00"}}, format="json")
    assert resp.status_code == 200


def test_non_latin_names_are_kept_apart(org):
    from apps.processing.normalize import normalize_vendor

    assert normalize_vendor("ООО Ромашка") == "ромашка"
    assert normalize_vendor("株式会社山田") == "山田"
    a = Vendor.objects.create(organization=org, name="ООО Ромашка")
    b = Vendor.objects.create(organization=org, name="株式会社山田")
    assert match_vendor(org.id, "Ромашка") == a and match_vendor(org.id, "山田") == b
    assert tasks.dedupe_key(org.id, "ООО Ромашка", "1") != tasks.dedupe_key(org.id, "株式会社山田", "1")


def test_unusable_name_is_never_a_vendor(org):
    doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    data = ExtractedData.objects.create(document=doc, vendor_name="—, .")
    assert learn_from_approval(data) is None
    assert match_vendor(org.id, "—, .") is None
