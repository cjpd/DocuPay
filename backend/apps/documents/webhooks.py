"""
Webhooks: push invoice events to customer systems.

Each event becomes one WebhookDeliveryLog per subscribed endpoint, delivered by the
deliver_webhook task with retries for about 24 hours. Every message carries a unique
event id (the receiver uses it to ignore repeats, so a retry never creates a second
bill) and an HMAC-SHA256 signature over "<timestamp>.<body>".

Endpoints are customer-supplied URLs, so delivery is SSRF-safe: HTTPS only, the host
is resolved and every address must be public, the connection goes to the address that
was checked (no DNS rebinding), and redirects are not followed.
"""
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Document, WebhookConfig, WebhookDeliveryLog

EVENTS = ("invoice.approved", "invoice.needs_review", "invoice.rejected", "invoice.failed")
PING = "ping"
# Seconds to wait before each retry: 1 min, 5 min, 30 min, 2 h, 6 h, 12 h (about 21 h in total).
RETRY_SCHEDULE = (60, 300, 1800, 7200, 21600, 43200)
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 2048
USER_AGENT = "DocuPay-Webhooks/1.0"


class UnsafeURL(ValueError):
    pass


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(secret: str, body: bytes, header: str, tolerance: int = 300, now: Optional[int] = None) -> bool:
    """What a receiver does (documented for customers, used in tests)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        timestamp = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((now or int(time.time())) - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body, timestamp), header)


# --- URL safety ---------------------------------------------------------------

def _allow_http() -> bool:
    return getattr(settings, "WEBHOOK_ALLOW_HTTP", False)


def _allow_private() -> bool:
    return getattr(settings, "WEBHOOK_ALLOW_PRIVATE", False)


def check_url_format(url: str) -> None:
    """Checks that need no network: scheme, host, port, literal private addresses."""
    parts = urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and _allow_http()):
        raise UnsafeURL("Use an https:// address.")
    if not parts.hostname:
        raise UnsafeURL("The address has no host name.")
    if parts.username or parts.password:
        raise UnsafeURL("Do not put a user name or password in the address.")
    try:
        parts.port
    except ValueError as exc:
        raise UnsafeURL("The port number is not valid.") from exc
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        if not _allow_private():
            raise UnsafeURL("Internal addresses are not allowed.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return  # a host name: resolved and checked at delivery
    _check_ip(literal)


def _check_ip(ip) -> None:
    if _allow_private():
        return
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        raise UnsafeURL("Private, internal and reserved addresses are not allowed.")


@dataclass
class Target:
    scheme: str
    host: str
    port: int
    path: str
    ip: str


def resolve_target(url: str) -> Target:
    check_url_format(url)
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL(f"The host name {parts.hostname} could not be found.") from exc
    addresses = {info[4][0] for info in infos}
    for address in addresses:  # every address must be public, not only the first
        _check_ip(ipaddress.ip_address(address))
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return Target(parts.scheme, parts.hostname, port, path, sorted(addresses)[0])


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to the checked IP address while verifying the certificate for the host name."""

    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def post(url: str, body: bytes, headers: dict) -> tuple:
    """POST without following redirects. Returns (status code, short response text)."""
    target = resolve_target(url)
    conn_cls = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
    conn = conn_cls(target.host, target.ip, target.port, TIMEOUT_SECONDS)
    try:
        conn.request("POST", target.path, body=body, headers={**headers, "Host": target.host})
        response = conn.getresponse()
        return response.status, response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")
    finally:
        conn.close()


# --- events ---------------------------------------------------------------------

def _money(value):
    return str(value) if value is not None else None


def document_payload(doc: Document) -> dict:
    data = getattr(doc, "extracted_data", None)
    meta = doc.processing_meta or {}
    approved_by = None
    if doc.status == Document.Status.APPROVED:
        if meta.get("decision") == "auto_approve":
            approved_by = "docupay"
        else:
            task = doc.review_tasks.filter(status="approved").select_related("reviewed_by").first()
            approved_by = task.reviewed_by.get_username() if task and task.reviewed_by else "person"
    frontend = getattr(settings, "FRONTEND_URL", "").rstrip("/")
    invoice = None
    if data:
        invoice = {
            "vendor": {"name": data.vendor_name, "tax_id": data.vendor_tax_id, "vendor_id": data.vendor_id},
            "invoice_number": data.invoice_number,
            "invoice_date": data.invoice_date.isoformat() if data.invoice_date else None,
            "due_date": data.due_date.isoformat() if data.due_date else None,
            "purchase_order": data.purchase_order,
            "customer_name": data.customer_name,
            "currency": data.currency,
            "subtotal": _money(data.subtotal),
            "tax_amount": _money(data.tax_amount),
            "total_amount": _money(data.total_amount),
            "amount_due": _money(data.amount_due),
            "payment": {"bank_account": data.bank_account, "bank_code": data.bank_code},
            "line_items": data.line_items,
        }
    return {
        "document": {
            "id": doc.id,
            "status": doc.status,
            "file_name": doc.file.name.rsplit("/", 1)[-1] if doc.file else "",
            "uploaded_at": doc.created_at.isoformat(),
            "approved_at": doc.approved_at.isoformat() if doc.approved_at else None,
            "approved_by": approved_by,
            "url": f"{frontend}/documents/?id={doc.id}" if frontend else None,
            "error": doc.error_message or None,
        },
        # Only approved invoices are final. Other events tell the receiver what is happening.
        "final": doc.status == Document.Status.APPROVED,
        "invoice": invoice,
        "failed_checks": [
            {"name": c.get("name"), "message": c.get("message")}
            for c in (data.validation if data else []) if c.get("status") == "fail"
        ],
    }


def _envelope(log: WebhookDeliveryLog) -> bytes:
    body = {
        "id": str(log.event_id),
        "type": log.event_type,
        "created_at": log.created_at.isoformat(),
        "organization_id": log.webhook_config.organization_id,
        "data": log.payload,
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def emit(document: Document, event_type: str) -> int:
    """Record the event for every active endpoint that wants it; deliver after the commit."""
    from apps.processing.tasks import deliver_webhook

    configs = [c for c in WebhookConfig.objects.filter(organization_id=document.organization_id, is_active=True)
               if event_type in (c.events or EVENTS)]
    if not configs:
        return 0
    payload = document_payload(document)
    for config in configs:
        log = WebhookDeliveryLog.objects.create(webhook_config=config, document=document, event_type=event_type,
                                                payload=payload, status=WebhookDeliveryLog.Status.PENDING)
        transaction.on_commit(lambda log_id=log.id: deliver_webhook.delay(log_id))
    return len(configs)


def send_test(config: WebhookConfig) -> WebhookDeliveryLog:
    from apps.processing.tasks import deliver_webhook

    log = WebhookDeliveryLog.objects.create(webhook_config=config, event_type=PING,
                                            payload={"message": "Test event from DocuPay"},
                                            status=WebhookDeliveryLog.Status.PENDING)
    transaction.on_commit(lambda: deliver_webhook.delay(log.id))
    return log


def attempt(log: WebhookDeliveryLog) -> bool:
    """One delivery attempt. Updates the log; returns True on a 2xx answer."""
    body = _envelope(log)
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "DocuPay-Event-Id": str(log.event_id),
        "DocuPay-Event-Type": log.event_type,
        "DocuPay-Signature": sign(log.webhook_config.secret, body, timestamp),
    }
    started = time.monotonic()
    log.attempts += 1
    try:
        status_code, text = post(log.webhook_config.target_url, body, headers)
        log.status_code = status_code
        log.last_error = "" if 200 <= status_code < 300 else f"HTTP {status_code}: {text[:300]}"
    except UnsafeURL as exc:
        log.status_code, log.last_error = None, f"Blocked: {exc}"
    except (OSError, http.client.HTTPException) as exc:
        log.status_code, log.last_error = None, f"Could not connect: {exc.__class__.__name__}: {exc}"[:500]
    log.response_ms = int((time.monotonic() - started) * 1000)
    log.success = log.status_code is not None and 200 <= log.status_code < 300
    if log.success:
        log.status, log.delivered_at, log.next_attempt_at = WebhookDeliveryLog.Status.DELIVERED, timezone.now(), None
    log.save()
    return log.success
