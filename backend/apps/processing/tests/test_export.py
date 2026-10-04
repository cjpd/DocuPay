"""CSV export (DP-25)."""
import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.documents.export import safe_cell
from apps.documents.models import Document, ExtractedData, ReviewTask

from .factories import make_document, make_org

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    org, user = make_org()
    client = APIClient()
    client.force_authenticate(user)
    return org, user, client


def _invoice(org, vendor="Müller GmbH", number="INV-1", total="119.00", status=Document.Status.APPROVED,
             auto=True, approved_days_ago=0, lines=None):
    doc = make_document(org, b"x", "inv.pdf", status=status)
    doc.processing_meta = {"decision": "auto_approve" if auto else "review"}
    doc.approved_at = timezone.now() - timedelta(days=approved_days_ago) if status == Document.Status.APPROVED else None
    doc.save()
    ExtractedData.objects.create(
        document=doc, vendor_name=vendor, invoice_number=number, currency="EUR",
        subtotal=Decimal("100.00"), tax_amount=Decimal("19.00"), total_amount=Decimal(total),
        line_items=lines if lines is not None else [
            {"description": "Paper", "quantity": "2", "unit_price": "25.00", "amount": "50.00"},
            {"description": "Toner", "quantity": "1", "unit_price": "50.00", "amount": "50.00"},
        ],
        validation=[{"name": "totals_math", "status": "pass"}],
    )
    return doc


def _rows(resp):
    assert resp.status_code == 200, resp
    body = b"".join(resp.streaming_content).decode("utf-8")
    assert body.startswith("﻿")
    return list(csv.reader(io.StringIO(body.lstrip("﻿"))))


def test_invoice_export_defaults_to_approved_only(setup):
    org, user, client = setup
    _invoice(org)
    _invoice(org, number="INV-2", status=Document.Status.REQUIRES_REVIEW)
    resp = client.get("/api/documents/export/")
    assert resp["Content-Type"] == "text/csv; charset=utf-8"
    assert 'attachment; filename="docupay-invoices-' in resp["Content-Disposition"]
    rows = _rows(resp)
    header, data = rows[0], rows[1:]
    assert header[:6] == ["Document ID", "Status", "Approved by", "Approved at", "Vendor", "Invoice number"]
    assert len(data) == 1
    row = dict(zip(header, data[0]))
    assert row["Vendor"] == "Müller GmbH"  # accents survive
    assert row["Total"] == "119.00"
    assert row["Approved by"] == "DocuPay (all checks passed)"
    assert row["Line items"] == "2"


def test_line_item_export(setup):
    org, user, client = setup
    _invoice(org)
    rows = _rows(client.get("/api/documents/export/?level=line"))
    assert rows[0][5:] == ["Line", "Description", "Quantity", "Unit price", "Amount"]
    assert [r[6] for r in rows[1:]] == ["Paper", "Toner"]


def test_approved_by_person_is_named(setup):
    org, user, client = setup
    doc = _invoice(org, auto=False)
    ReviewTask.objects.create(document=doc, status=ReviewTask.STATUS_APPROVED, reviewed_by=user)
    rows = _rows(client.get("/api/documents/export/"))
    assert dict(zip(rows[0], rows[1]))["Approved by"] == user.username


def test_formula_injection_is_neutralized(setup):
    org, user, client = setup
    _invoice(org, vendor='=HYPERLINK("http://evil.example","click")', number="+SUM(A1)")
    row = dict(zip(*_rows(client.get("/api/documents/export/"))[:2]))
    assert row["Vendor"].startswith("'=")
    assert row["Invoice number"] == "'+SUM(A1)"


@pytest.mark.parametrize("value,expected", [
    ("=1+1", "'=1+1"), ("@cmd", "'@cmd"), ("-5.00", "-5.00"), ("-x", "'-x"), (None, ""), (Decimal("1.50"), "1.50"),
])
def test_safe_cell(value, expected):
    assert safe_cell(value) == expected


def test_date_range_uses_approval_date(setup):
    org, user, client = setup
    _invoice(org, number="OLD", approved_days_ago=40)
    _invoice(org, number="NEW", approved_days_ago=1)
    since = (timezone.now() - timedelta(days=7)).date().isoformat()
    rows = _rows(client.get(f"/api/documents/export/?from={since}"))
    assert [r[5] for r in rows[1:]] == ["NEW"]


def test_status_all_and_lists(setup):
    org, user, client = setup
    _invoice(org)
    _invoice(org, number="R", status=Document.Status.REQUIRES_REVIEW)
    make_document(org, b"x", status=Document.Status.FAILED)
    assert len(_rows(client.get("/api/documents/export/?status=all"))) == 4
    assert len(_rows(client.get("/api/documents/export/?status=approved,requires_review"))) == 3


def test_export_is_tenant_scoped(setup):
    org, user, client = setup
    other, _ = make_org(slug="other")
    _invoice(other, vendor="Secret Vendor")
    rows = _rows(client.get("/api/documents/export/?status=all"))
    assert len(rows) == 1  # header only


@pytest.mark.parametrize("query", ["level=pdf", "status=bogus", "from=2026-13-01"])
def test_bad_parameters(setup, query):
    org, user, client = setup
    assert client.get(f"/api/documents/export/?{query}").status_code == 400


def test_empty_export_has_header(setup):
    org, user, client = setup
    rows = _rows(client.get("/api/documents/export/"))
    assert rows == [rows[0]] and rows[0][0] == "Document ID"
