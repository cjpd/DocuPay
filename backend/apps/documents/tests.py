import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.documents.models import Document, ExtractedData, ReviewTask
from apps.organizations.models import OrgMembership, Organization

User = get_user_model()


def _make_user_and_org(username="user1", org_slug="org1"):
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@test.com")
    org = Organization.objects.create(name=org_slug, slug=org_slug)
    OrgMembership.objects.create(organization=org, user=user, role="member")
    return user, org


def _fake_file(name="invoice.jpg", content=b"fake"):
    f = io.BytesIO(content)
    f.name = name
    return f


class TestAuth(APITestCase):
    def test_unauthenticated_document_list_returns_401(self):
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_upload_returns_401(self):
        response = self.client.post("/api/documents/upload/", {})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_analytics_returns_401(self):
        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TestDocumentUpload(APITestCase):
    def setUp(self):
        self.user, self.org = _make_user_and_org()
        self.client.force_authenticate(user=self.user)

    @patch("apps.documents.views.process_document.delay")
    def test_upload_creates_document_and_queues_task(self, mock_delay):
        response = self.client.post(
            "/api/documents/upload/",
            {"file": _fake_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.first()
        self.assertEqual(doc.status, Document.Status.PROCESSING)
        self.assertEqual(doc.organization, self.org)
        mock_delay.assert_called_once_with(doc.id)

    def test_upload_without_file_returns_400(self):
        response = self.client.post("/api/documents/upload/", {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_without_org_returns_403(self):
        user_no_org = User.objects.create_user(username="noorg", password="pass", email="noorg@test.com")
        self.client.force_authenticate(user=user_no_org)
        response = self.client.post(
            "/api/documents/upload/",
            {"file": _fake_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("apps.documents.views.process_document.delay")
    def test_documents_scoped_to_org(self, _mock):
        # Upload one doc for self.user's org
        self.client.post("/api/documents/upload/", {"file": _fake_file()}, format="multipart")

        # A user in a different org should see 0 docs
        other_user, _ = _make_user_and_org(username="other", org_slug="other-org")
        self.client.force_authenticate(user=other_user)
        response = self.client.get("/api/documents/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data if isinstance(response.data, list) else response.data.get("results", [])
        self.assertEqual(len(data), 0)


class TestReviewTask(APITestCase):
    def setUp(self):
        self.user, self.org = _make_user_and_org()
        self.client.force_authenticate(user=self.user)
        self.doc = Document.objects.create(
            organization=self.org,
            uploaded_by=self.user,
            status=Document.Status.REQUIRES_REVIEW,
        )
        self.extracted = ExtractedData.objects.create(
            document=self.doc,
            vendor_name="ACME Corp",
            total_amount="500.00",
            overall_confidence=0.6,
        )
        self.task = ReviewTask.objects.create(document=self.doc, status=ReviewTask.STATUS_PENDING)

    def test_approve_task_updates_status(self):
        response = self.client.post(f"/api/documents/reviews/{self.task.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.doc.refresh_from_db()
        self.assertEqual(self.task.status, ReviewTask.STATUS_APPROVED)
        self.assertEqual(self.doc.status, Document.Status.APPROVED)
        self.assertIsNotNone(self.doc.approved_at)

    def test_approve_applies_corrections(self):
        response = self.client.post(
            f"/api/documents/reviews/{self.task.id}/approve/",
            {"corrections": {"vendor_name": "Corrected Corp"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.extracted.refresh_from_db()
        self.assertEqual(self.extracted.vendor_name, "Corrected Corp")

    def test_reject_task_updates_status(self):
        response = self.client.post(f"/api/documents/reviews/{self.task.id}/reject/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, ReviewTask.STATUS_REJECTED)

    def test_cannot_approve_already_approved_task(self):
        self.task.status = ReviewTask.STATUS_APPROVED
        self.task.save()
        response = self.client.post(f"/api/documents/reviews/{self.task.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TestAnalytics(APITestCase):
    def setUp(self):
        self.user, self.org = _make_user_and_org()
        self.client.force_authenticate(user=self.user)

    def test_analytics_returns_expected_shape(self):
        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ("docs_processed_24h", "pending_review", "avg_confidence", "docs_processed_24h_delta"):
            self.assertIn(key, response.data)

    def test_analytics_counts_pending_review(self):
        doc = Document.objects.create(
            organization=self.org, uploaded_by=self.user, status=Document.Status.REQUIRES_REVIEW
        )
        ReviewTask.objects.create(document=doc, status=ReviewTask.STATUS_PENDING)

        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.data["pending_review"], 1)

    def test_analytics_counts_processed_docs(self):
        Document.objects.create(
            organization=self.org, uploaded_by=self.user, status=Document.Status.APPROVED
        )
        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.data["docs_processed_24h"], 1)

    def test_analytics_reflects_avg_confidence(self):
        doc = Document.objects.create(
            organization=self.org, uploaded_by=self.user, status=Document.Status.APPROVED
        )
        ExtractedData.objects.create(document=doc, overall_confidence=0.80)

        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.data["avg_confidence"], 80.0)

    def test_analytics_scoped_to_org(self):
        # Doc in another org should not appear in analytics
        other_user, other_org = _make_user_and_org(username="other2", org_slug="other-org2")
        Document.objects.create(
            organization=other_org, uploaded_by=other_user, status=Document.Status.APPROVED
        )

        response = self.client.get("/api/documents/analytics/")
        self.assertEqual(response.data["docs_processed_24h"], 0)
