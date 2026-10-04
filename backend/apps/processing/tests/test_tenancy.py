"""Cross-tenant tests for every endpoint (DP-15)."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.documents.models import Document, ExtractedData, ReviewTask, WebhookConfig, WebhookDeliveryLog
from apps.organizations.models import OrgMembership

from .factories import INVOICE_TEXT, make_document, make_org

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    """Org A (victim) with one of everything, and a user from org B (attacker)."""
    org_a, user_a = make_org(slug="a")
    org_b, user_b = make_org(slug="b")
    doc = make_document(org_a, INVOICE_TEXT.encode(), status=Document.Status.REQUIRES_REVIEW)
    data = ExtractedData.objects.create(document=doc, total_amount="10.00")
    task = ReviewTask.objects.create(document=doc)
    hook = WebhookConfig.objects.create(organization=org_a, target_url="https://a.example.com/hook", secret="s3cret")
    log = WebhookDeliveryLog.objects.create(webhook_config=hook, document=doc)
    from apps.documents.models import Vendor

    vendor = Vendor.objects.create(organization=org_a, name="Victim Vendor", proposed_bank_account="DE89370400440532013000")
    attacker = APIClient()
    attacker.force_authenticate(user_b)
    owner = APIClient()
    owner.force_authenticate(user_a)
    return {"org_a": org_a, "org_b": org_b, "user_a": user_a, "user_b": user_b, "doc": doc, "data": data,
            "task": task, "hook": hook, "log": log, "vendor": vendor, "attacker": attacker, "owner": owner}


DETAIL_URLS = [
    ("/api/documents/{doc}/", "get"),
    ("/api/documents/{doc}/", "delete"),
    ("/api/documents/{doc}/reprocess/", "post"),
    ("/api/documents/extracted/{data}/", "get"),
    ("/api/documents/reviews/{task}/", "get"),
    ("/api/documents/reviews/{task}/approve/", "post"),
    ("/api/documents/reviews/{task}/reject/", "post"),
    ("/api/documents/webhooks/{hook}/", "get"),
    ("/api/documents/webhooks/{hook}/", "patch"),
    ("/api/documents/webhooks/{hook}/", "delete"),
    ("/api/documents/webhook-deliveries/{log}/", "get"),
    ("/api/documents/{doc}/file/", "get"),
    ("/api/documents/{doc}/preview/", "get"),
    ("/api/documents/vendors/{vendor}/", "get"),
    ("/api/documents/vendors/{vendor}/", "patch"),
    ("/api/documents/vendors/{vendor}/bank-account/", "post"),
    ("/api/documents/webhooks/{hook}/test/", "post"),
    ("/api/documents/webhooks/{hook}/rotate-secret/", "post"),
    ("/api/documents/webhook-deliveries/{log}/retry/", "post"),
    ("/api/organizations/{org_a}/", "get"),
    ("/api/organizations/{org_a}/", "patch"),
    ("/api/organizations/{org_a}/", "delete"),
]


@pytest.mark.parametrize("url,method", DETAIL_URLS)
def test_other_tenant_gets_404(world, url, method):
    ids = {k: world[k].id for k in ("doc", "data", "task", "hook", "log", "vendor", "org_a")}
    resp = getattr(world["attacker"], method)(url.format(**ids), {}, format="json")
    assert resp.status_code == 404, resp.content
    assert Document.objects.filter(id=world["doc"].id, status=Document.Status.REQUIRES_REVIEW).exists()
    assert WebhookConfig.objects.filter(id=world["hook"].id).exists()


@pytest.mark.parametrize("url", [
    "/api/documents/", "/api/documents/extracted/", "/api/documents/reviews/",
    "/api/documents/webhooks/", "/api/documents/webhook-deliveries/", "/api/organizations/", "/api/documents/vendors/",
])
def test_lists_exclude_other_tenant(world, url):
    seen = {row["id"] for row in world["attacker"].get(url).data["results"]}
    # Ids of different tables can collide; only compare within the same table.
    if url == "/api/organizations/":
        assert seen == {world["org_b"].id}
    else:
        assert seen == set()
    assert len(world["owner"].get(url).data["results"]) == 1


def test_webhook_cannot_be_created_for_another_tenant(world):
    world["user_b"].memberships.update(role="owner")
    resp = world["attacker"].post("/api/documents/webhooks/", {
        "organization": world["org_a"].id, "target_url": "https://evil.example.com", "secret": "x"}, format="json")
    assert resp.status_code == 201
    assert WebhookConfig.objects.get(id=resp.data["id"]).organization_id == world["org_b"].id


def test_webhook_secret_is_never_returned(world):
    resp = world["owner"].get(f"/api/documents/webhooks/{world['hook'].id}/")
    assert resp.status_code == 200
    assert "secret" not in resp.data


def test_review_task_cannot_be_edited_directly(world):
    url = f"/api/documents/reviews/{world['task'].id}/"
    assert world["owner"].patch(url, {"status": "approved"}, format="json").status_code == 405
    world["task"].refresh_from_db()
    assert world["task"].status == ReviewTask.STATUS_PENDING


def test_member_cannot_change_or_delete_organization(world):
    member = make_org(slug="m")[1]
    OrgMembership.objects.create(organization=world["org_a"], user=member, role="member")
    client = APIClient()
    client.force_authenticate(member)
    url = f"/api/organizations/{world['org_a'].id}/"
    assert client.patch(url, {"auto_approve_threshold": 0.5}, format="json").status_code == 403
    assert client.delete(url).status_code == 403
    OrgMembership.objects.filter(organization=world["org_a"], user=world["user_a"]).update(role="owner")
    assert world["owner"].patch(url, {"auto_approve_threshold": 0.95}, format="json").status_code == 200


def test_threshold_is_validated(world):
    OrgMembership.objects.filter(user=world["user_a"]).update(role="owner")
    resp = world["owner"].patch(f"/api/organizations/{world['org_a'].id}/", {"auto_approve_threshold": 0.1}, format="json")
    assert resp.status_code == 400


def test_users_endpoint_is_read_only(world):
    assert world["owner"].post("/api/users/", {"username": "x"}, format="json").status_code == 405


class TestMultiOrgUser:
    @pytest.fixture
    def client(self, world):
        OrgMembership.objects.create(organization=world["org_b"], user=world["user_a"])
        return world["owner"]

    def test_upload_requires_organization_choice(self, world, client):
        resp = client.post("/api/documents/upload/", {"file": SimpleUploadedFile("a.txt", b"x")})
        assert resp.status_code == 400

    def test_header_selects_organization(self, world, client, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post("/api/documents/upload/", {"file": SimpleUploadedFile("a.txt", b"x")},
                               HTTP_X_ORGANIZATION_ID=str(world["org_b"].id))
        assert resp.status_code == 201
        assert Document.objects.get(id=resp.data["id"]).organization_id == world["org_b"].id

    def test_header_scopes_reads(self, world, client):
        assert client.get("/api/documents/", HTTP_X_ORGANIZATION_ID=str(world["org_b"].id)).data["count"] == 0
        assert client.get("/api/documents/", HTTP_X_ORGANIZATION_ID=str(world["org_a"].id)).data["count"] == 1
        assert client.get("/api/documents/").data["count"] == 1  # no header: all memberships

    def test_header_for_foreign_org_is_refused(self, world, client):
        _, stranger = make_org(slug="z")
        org_z = stranger.memberships.first().organization_id
        assert client.get("/api/documents/", HTTP_X_ORGANIZATION_ID=str(org_z)).status_code == 403
        assert client.get("/api/documents/", HTTP_X_ORGANIZATION_ID="abc").status_code == 400
