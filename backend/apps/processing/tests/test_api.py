import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.documents.models import Document, ReviewTask

from .factories import INVOICE_TEXT, make_document, make_org

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    org, user = make_org()
    client = APIClient()
    client.force_authenticate(user)
    return org, user, client


def test_upload_queues_processing_after_commit(setup, django_capture_on_commit_callbacks):
    org, user, client = setup
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post("/api/documents/upload/", {"file": SimpleUploadedFile("inv.txt", INVOICE_TEXT.encode())})
    assert resp.status_code == 201
    doc = Document.objects.get(id=resp.data["id"])
    # Heuristic provider in tests: it extracts the invoice and the checks pass.
    assert doc.status in (Document.Status.APPROVED, Document.Status.REQUIRES_REVIEW)
    assert doc.extracted_data.invoice_number == "INV-1001"


def test_review_list_route(setup):
    org, user, client = setup
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    ReviewTask.objects.create(document=doc)
    resp = client.get("/api/documents/reviews/")
    assert resp.status_code == 200
    assert resp.data["count"] == 1


def _task(org):
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    return ReviewTask.objects.create(document=doc)


def test_approve_rejects_non_editable_fields(setup):
    org, user, client = setup
    other_org, _ = make_org(slug="other")
    other_doc = make_document(other_org, b"x")
    task = _task(org)
    resp = client.post(f"/api/documents/reviews/{task.id}/approve/",
                       {"corrections": {"document_id": other_doc.id}}, format="json")
    assert resp.status_code == 400
    task.refresh_from_db()
    assert task.status == ReviewTask.STATUS_PENDING


def test_approve_validates_values(setup):
    org, user, client = setup
    task = _task(org)
    resp = client.post(f"/api/documents/reviews/{task.id}/approve/",
                       {"corrections": {"invoice_date": "not a date"}}, format="json")
    assert resp.status_code == 400


def test_approve_applies_corrections(setup):
    org, user, client = setup
    task = _task(org)
    resp = client.post(f"/api/documents/reviews/{task.id}/approve/",
                       {"corrections": {"total_amount": "99.50", "invoice_number": "INV-9"}}, format="json")
    assert resp.status_code == 200
    task.document.refresh_from_db()
    assert task.document.status == Document.Status.APPROVED
    assert str(task.document.extracted_data.total_amount) == "99.50"


def test_reprocess(setup, django_capture_on_commit_callbacks):
    org, user, client = setup
    doc = make_document(org, INVOICE_TEXT.encode(), status=Document.Status.FAILED)
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(f"/api/documents/{doc.id}/reprocess/")
    assert resp.status_code == 202
    doc.refresh_from_db()
    assert doc.status != Document.Status.FAILED


def test_other_org_cannot_see_documents(setup):
    org, user, client = setup
    other_org, _ = make_org(slug="other")
    doc = make_document(other_org, b"x")
    assert client.get(f"/api/documents/{doc.id}/").status_code == 404
