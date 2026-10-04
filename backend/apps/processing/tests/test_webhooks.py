"""Webhooks (DP-16): events, signatures, retries, SSRF safety, API."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.documents import webhooks
from apps.documents.models import Document, ExtractedData, ReviewTask, WebhookConfig, WebhookDeliveryLog
from apps.processing import tasks
from apps.processing.providers.fake import FakeProvider

from .factories import CLEAN_INVOICE, INVOICE_TEXT, make_document, make_org

pytestmark = pytest.mark.django_db
LOCAL = override_settings(WEBHOOK_ALLOW_HTTP=True, WEBHOOK_ALLOW_PRIVATE=True)


class Receiver:
    """A real HTTP endpoint on 127.0.0.1 that records requests and answers with a chosen status."""

    def __init__(self, statuses=(200,)):
        self.requests, self.statuses = [], list(statuses)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"headers": dict(self.headers), "body": body})
                code = outer.statuses.pop(0) if len(outer.statuses) > 1 else outer.statuses[0]
                self.send_response(code)
                if code in (301, 302):
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hooks/docupay"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


@pytest.fixture
def org():
    org, user = make_org()
    user.memberships.update(role="owner")
    return org


def _config(org, url, **kw):
    return WebhookConfig.objects.create(organization=org, target_url=url, secret="whsec_test", **kw)


def _process(org, data, capture):
    doc = make_document(org, INVOICE_TEXT.encode())
    with capture(execute=True), mock.patch.object(tasks, "get_provider", return_value=FakeProvider(fast=data, strong=data)):
        tasks.process_document.apply(args=[doc.id])
    doc.refresh_from_db()
    return doc


# --- delivery ---------------------------------------------------------------------

@LOCAL
def test_approved_invoice_is_delivered_and_signed(org, receiver, django_capture_on_commit_callbacks):
    _config(org, receiver.url)
    doc = _process(org, CLEAN_INVOICE, django_capture_on_commit_callbacks)
    assert doc.status == Document.Status.APPROVED
    assert len(receiver.requests) == 1
    req = receiver.requests[0]
    assert webhooks.verify("whsec_test", req["body"], req["headers"]["DocuPay-Signature"])
    assert not webhooks.verify("wrong-secret", req["body"], req["headers"]["DocuPay-Signature"])
    event = json.loads(req["body"])
    assert event["type"] == "invoice.approved" == req["headers"]["DocuPay-Event-Type"]
    assert event["id"] == req["headers"]["DocuPay-Event-Id"]
    assert event["data"]["final"] is True
    assert event["data"]["invoice"]["total_amount"] == "324.00"
    assert event["data"]["document"]["approved_by"] == "docupay"
    log = WebhookDeliveryLog.objects.get()
    assert log.status == "delivered" and log.status_code == 200 and log.attempts == 1


@LOCAL
def test_needs_review_event_is_not_final(org, receiver, django_capture_on_commit_callbacks):
    _config(org, receiver.url)
    _process(org, {**CLEAN_INVOICE, "total_amount": 1.0}, django_capture_on_commit_callbacks)
    event = json.loads(receiver.requests[0]["body"])
    assert event["type"] == "invoice.needs_review" and event["data"]["final"] is False
    assert any(c["name"] == "totals_math" for c in event["data"]["failed_checks"])


@LOCAL
def test_only_subscribed_events_are_sent(org, receiver, django_capture_on_commit_callbacks):
    _config(org, receiver.url, events=["invoice.approved"])
    _process(org, {**CLEAN_INVOICE, "total_amount": 1.0}, django_capture_on_commit_callbacks)
    assert receiver.requests == []


@LOCAL
def test_failed_document_event(org, receiver, django_capture_on_commit_callbacks):
    _config(org, receiver.url)
    doc = make_document(org, b"%PDF broken", "x.pdf")
    with django_capture_on_commit_callbacks(execute=True):
        tasks.process_document.apply(args=[doc.id])
    assert json.loads(receiver.requests[0]["body"])["type"] == "invoice.failed"


@LOCAL
def test_person_approval_and_rejection_events(org, receiver, django_capture_on_commit_callbacks):
    _config(org, receiver.url)
    client = APIClient()
    client.force_authenticate(org.memberships.first().user)
    for verb in ("approve", "reject"):
        doc = make_document(org, b"x", status=Document.Status.REQUIRES_REVIEW)
        ExtractedData.objects.create(document=doc, vendor_name="Acme", invoice_number=verb, invoice_date="2026-09-01",
                                     total_amount="10.00")
        task = ReviewTask.objects.create(document=doc)
        with django_capture_on_commit_callbacks(execute=True):
            assert client.post(f"/api/documents/reviews/{task.id}/{verb}/", {"corrections": {}}, format="json").status_code == 200
    types = [json.loads(r["body"])["type"] for r in receiver.requests]
    assert types == ["invoice.approved", "invoice.rejected"]
    assert json.loads(receiver.requests[0]["body"])["data"]["document"]["approved_by"] == org.memberships.first().user.username


@LOCAL
def test_retries_keep_the_same_event_id_then_succeed(org, django_capture_on_commit_callbacks):
    r = Receiver(statuses=[500, 503, 200])
    try:
        _config(org, r.url)
        _process(org, CLEAN_INVOICE, django_capture_on_commit_callbacks)
        ids = {req["headers"]["DocuPay-Event-Id"] for req in r.requests}
        assert len(r.requests) == 3 and len(ids) == 1  # the receiver can ignore repeats
        log = WebhookDeliveryLog.objects.get()
        assert log.status == "delivered" and log.attempts == 3
    finally:
        r.close()


@LOCAL
def test_gives_up_after_the_schedule(org, django_capture_on_commit_callbacks):
    r = Receiver(statuses=[500])
    try:
        _config(org, r.url)
        _process(org, CLEAN_INVOICE, django_capture_on_commit_callbacks)
        log = WebhookDeliveryLog.objects.get()
        assert log.status == "failed"
        assert log.attempts == len(webhooks.RETRY_SCHEDULE) + 1
        assert "HTTP 500" in log.last_error
    finally:
        r.close()


@LOCAL
def test_redirects_are_not_followed(org, django_capture_on_commit_callbacks):
    r = Receiver(statuses=[302])
    try:
        _config(org, r.url)
        with mock.patch.object(webhooks, "RETRY_SCHEDULE", ()):
            _process(org, CLEAN_INVOICE, django_capture_on_commit_callbacks)
        log = WebhookDeliveryLog.objects.get()
        assert log.status == "failed" and log.status_code == 302 and len(r.requests) == 1
    finally:
        r.close()


def test_webhook_errors_never_break_processing(org, django_capture_on_commit_callbacks):
    _config(org, "https://example.com/hook")
    with mock.patch.object(webhooks, "emit", side_effect=RuntimeError("boom")):
        doc = _process(org, CLEAN_INVOICE, django_capture_on_commit_callbacks)
    assert doc.status == Document.Status.APPROVED


# --- SSRF -----------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://example.com/hook",  # not https
    "https://localhost/hook",
    "https://127.0.0.1/hook",
    "https://10.0.0.5/hook",
    "https://192.168.1.1/hook",
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://[::1]/hook",
    "https://[::ffff:127.0.0.1]/hook",
    "https://metadata.internal/hook",
    "https://user:pass@example.com/hook",
])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(webhooks.UnsafeURL):
        webhooks.check_url_format(url)


def test_host_name_resolving_to_a_private_address_is_blocked():
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
    with mock.patch.object(socket, "getaddrinfo", return_value=fake):
        with pytest.raises(webhooks.UnsafeURL, match="Private"):
            webhooks.resolve_target("https://innocent-looking.example.com/hook")


def test_any_private_address_among_several_is_blocked():
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    with mock.patch.object(socket, "getaddrinfo", return_value=fake):
        with pytest.raises(webhooks.UnsafeURL):
            webhooks.resolve_target("https://rebind.example.com/hook")


def test_connection_goes_to_the_checked_address():
    """No DNS rebinding: the socket connects to the IP that passed the check."""
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with mock.patch.object(socket, "getaddrinfo", return_value=fake), \
         mock.patch.object(socket, "create_connection", side_effect=OSError("stop")) as connect:
        with pytest.raises(OSError):
            webhooks.post("https://shop.example.com/hook", b"{}", {})
    assert connect.call_args[0][0] == ("93.184.216.34", 443)


def test_signature_rejects_old_timestamps():
    header = webhooks.sign("s", b"{}", 1000)
    assert webhooks.verify("s", b"{}", header, now=1100)
    assert not webhooks.verify("s", b"{}", header, now=5000)  # replay
    assert not webhooks.verify("s", b"{}", "garbage")


# --- API ------------------------------------------------------------------------

@pytest.fixture
def clients(org):
    from apps.organizations.models import OrgMembership

    owner = APIClient()
    owner.force_authenticate(org.memberships.get(role="owner").user)
    member_user = make_org(slug="m")[1]
    OrgMembership.objects.create(organization=org, user=member_user, role="member")
    member = APIClient()
    member.force_authenticate(member_user)
    member.credentials(HTTP_X_ORGANIZATION_ID=str(org.id))
    return owner, member


def test_secret_is_generated_and_shown_once(org, clients):
    owner, _ = clients
    resp = owner.post("/api/documents/webhooks/", {"target_url": "https://example.com/hook", "secret": "mine"}, format="json")
    assert resp.status_code == 201
    secret = resp.data["signing_secret"]
    assert secret.startswith("whsec_") and secret != "mine"
    again = owner.get(f"/api/documents/webhooks/{resp.data['id']}/").data
    assert again["signing_secret"] is None and "secret" not in again
    rotated = owner.post(f"/api/documents/webhooks/{resp.data['id']}/rotate-secret/").data["signing_secret"]
    assert rotated.startswith("whsec_") and rotated != secret


def test_only_admins_manage_webhooks(org, clients):
    owner, member = clients
    body = {"target_url": "https://example.com/hook"}
    assert member.post("/api/documents/webhooks/", body, format="json").status_code == 403
    config_id = owner.post("/api/documents/webhooks/", body, format="json").data["id"]
    assert member.get("/api/documents/webhooks/").data["count"] == 1  # members can see them
    assert member.patch(f"/api/documents/webhooks/{config_id}/", {"is_active": False}, format="json").status_code == 403
    assert member.post(f"/api/documents/webhooks/{config_id}/test/").status_code == 403


@pytest.mark.parametrize("body", [
    {"target_url": "https://10.0.0.1/hook"},
    {"target_url": "https://example.com/hook", "events": ["invoice.paid"]},
    {"target_url": "https://example.com/hook", "events": []},
])
def test_webhook_validation(org, clients, body):
    assert clients[0].post("/api/documents/webhooks/", body, format="json").status_code == 400


@LOCAL
def test_test_button_and_retry(org, clients, receiver, django_capture_on_commit_callbacks):
    owner, _ = clients
    config = _config(org, receiver.url)
    with django_capture_on_commit_callbacks(execute=True):
        resp = owner.post(f"/api/documents/webhooks/{config.id}/test/")
    assert resp.status_code == 202
    assert json.loads(receiver.requests[0]["body"])["type"] == "ping"
    log = WebhookDeliveryLog.objects.get()
    assert owner.post(f"/api/documents/webhook-deliveries/{log.id}/retry/").status_code == 409  # already delivered
    WebhookDeliveryLog.objects.filter(id=log.id).update(status="failed")
    with django_capture_on_commit_callbacks(execute=True):
        assert owner.post(f"/api/documents/webhook-deliveries/{log.id}/retry/").status_code == 202
    assert len(receiver.requests) == 2
    assert receiver.requests[0]["headers"]["DocuPay-Event-Id"] == receiver.requests[1]["headers"]["DocuPay-Event-Id"]


def test_delivery_log_is_tenant_scoped(org, clients):
    other, _ = make_org(slug="other")
    foreign = WebhookDeliveryLog.objects.create(webhook_config=_config(other, "https://example.com/h"), event_type="ping")
    owner, _ = clients
    assert owner.get(f"/api/documents/webhook-deliveries/{foreign.id}/").status_code == 404
    assert owner.get("/api/documents/webhook-deliveries/").data["count"] == 0
