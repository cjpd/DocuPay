"""Endpoints the frontend depends on: stats, file download, review queue with its document."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.documents.models import Document, ExtractedData, ReviewTask

from .factories import INVOICE_TEXT, image_bytes, make_document, make_org, text_pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    org, user = make_org()
    client = APIClient()
    client.force_authenticate(user)
    return org, client


def _approved(org, total, currency="USD", auto=True):
    doc = make_document(org, b"x", status=Document.Status.APPROVED)
    doc.processing_meta = {"decision": "auto_approve" if auto else "review", "total_cost_usd": "0.0040"}
    doc.save()
    ExtractedData.objects.create(document=doc, total_amount=Decimal(total), currency=currency)
    return doc


def test_stats_are_computed_from_real_documents(setup):
    org, client = setup
    _approved(org, "100.00")
    _approved(org, "50.50")
    _approved(org, "20.00", currency="EUR", auto=False)
    review = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
    ReviewTask.objects.create(document=review)
    make_document(org, b"x", status=Document.Status.FAILED)
    other_org, _ = make_org(slug="other")
    _approved(other_org, "999.00")  # must not be counted

    data = client.get("/api/documents/stats/").data
    assert data["received"] == 5
    assert data["auto_approved"] == 2
    assert data["approved_by_person"] == 1
    assert data["needs_review"] == 1
    assert data["failed"] == 1
    assert data["straight_through_rate"] == 0.5  # 2 of 4 processed
    assert data["approved_value"] == {"EUR": "20.00", "USD": "150.50"}
    assert data["processing_cost_usd"] == "0.0120"
    assert data["series"][-1]["received"] == 5
    assert len(data["series"]) == 14


def test_stats_empty(setup):
    org, client = setup
    data = client.get("/api/documents/stats/").data
    assert data["received"] == 0
    assert data["straight_through_rate"] is None


@pytest.mark.parametrize("content,name,content_type", [
    (lambda: text_pdf(), "a.pdf", "application/pdf"),
    (lambda: image_bytes("PNG"), "a.png", "image/png"),
    (lambda: INVOICE_TEXT.encode(), "a.txt", "text/plain; charset=utf-8"),
])
def test_file_download(setup, content, name, content_type):
    org, client = setup
    doc = make_document(org, content(), name)
    resp = client.get(f"/api/documents/{doc.id}/file/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == content_type
    assert resp["Content-Security-Policy"] == "sandbox"
    assert b"".join(resp.streaming_content) == doc.file.open("rb").read()


def test_file_download_is_tenant_scoped(setup):
    org, client = setup
    other_org, _ = make_org(slug="other")
    doc = make_document(other_org, b"secret", "a.txt")
    assert client.get(f"/api/documents/{doc.id}/file/").status_code == 404


def test_document_has_no_direct_storage_url(setup):
    org, client = setup
    doc = make_document(org, b"x", "invoice.txt")
    data = client.get(f"/api/documents/{doc.id}/").data
    assert "file" not in data
    assert data["file_name"].startswith("invoice")


def test_review_queue_includes_document_and_filters(setup):
    org, client = setup
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    ExtractedData.objects.create(document=doc, total_amount=Decimal("5.00"),
                                 validation=[{"name": "totals_math", "status": "fail"}])
    task = ReviewTask.objects.create(document=doc)
    ReviewTask.objects.create(document=make_document(org, b"y"), status=ReviewTask.STATUS_APPROVED)
    data = client.get("/api/documents/reviews/?status=pending").data
    assert data["count"] == 1
    row = data["results"][0]
    assert row["id"] == task.id
    assert row["document_detail"]["extracted_data"]["validation"][0]["name"] == "totals_math"
    assert row["document_detail"]["review_task_id"] == task.id


def test_document_filters(setup):
    org, client = setup
    a = make_document(org, b"x", "acme.txt", status=Document.Status.APPROVED)
    ExtractedData.objects.create(document=a, vendor_name="Acme Supplies", invoice_number="INV-9")
    make_document(org, b"x", "other.txt", status=Document.Status.FAILED)
    assert client.get("/api/documents/?status=failed").data["count"] == 1
    assert client.get("/api/documents/?status=failed,approved").data["count"] == 2
    assert client.get("/api/documents/?q=acme").data["results"][0]["id"] == a.id
    assert client.get("/api/documents/?q=inv-9").data["count"] == 1


def test_preview_pages(setup):
    org, client = setup
    doc = make_document(org, text_pdf(pages=2), "two.pdf")
    resp = client.get(f"/api/documents/{doc.id}/preview/?page=2")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/jpeg"
    assert resp["X-Page-Count"] == "2"
    assert resp.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/documents/{doc.id}/preview/?page=3").status_code == 404
    txt = make_document(org, INVOICE_TEXT.encode(), "a.txt")
    assert b"INV-1001" in client.get(f"/api/documents/{txt.id}/preview/").content


def test_preview_is_tenant_scoped(setup):
    org, client = setup
    other_org, _ = make_org(slug="other")
    doc = make_document(other_org, text_pdf(), "a.pdf")
    assert client.get(f"/api/documents/{doc.id}/preview/").status_code == 404
