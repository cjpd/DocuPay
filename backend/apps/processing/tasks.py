import logging
import re
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.documents.models import Document, ExtractedData, ReviewTask
from apps.organizations.models import Organization

from .errors import PermanentProcessingError, ProcessingError, TransientProcessingError
from .ingest import load_document
from .normalize import normalize_invoice_number, normalize_vendor
from .pipeline import AUTO_APPROVE, REVIEW, run_pipeline
from .providers import get_provider
from .validation import MAX_STORABLE_AMOUNT, VendorHistory, validate

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
# A run that has not finished after this long is treated as dead (worker killed by the
# hard time limit, out of memory, or lost) and marked FAILED by fail_stale_documents.
STALE_AFTER = timedelta(minutes=15)
GENERIC_ERROR = "Processing failed because of an internal error. Use Reprocess to try again."


def dedupe_key(organization_id: int, vendor_name, invoice_number) -> str:
    """Normalized key for duplicate detection: false matches only send a document to review,
    missed matches can pay an invoice twice, so normalization is generous."""
    if not (vendor_name and invoice_number):
        return ""
    return f"{organization_id}:{normalize_vendor(vendor_name)}:{normalize_invoice_number(invoice_number)}"[:400]


def _storable(amount):
    """Amounts the column cannot hold are saved as null (the raw value stays in raw_extraction;
    the amount_limit check has already sent the document to review)."""
    return amount if amount is None or abs(amount) <= MAX_STORABLE_AMOUNT else None


def _mark_failed(document_id: int, task_id: str, message: str) -> None:
    if Document.objects.filter(id=document_id, processing_task_id=task_id).update(
        status=Document.Status.FAILED, error_message=message[:2000], updated_at=timezone.now()
    ):
        _emit(document_id, "invoice.failed")


def _duplicate_checker(doc: Document):
    def is_duplicate(ex) -> bool:
        key = dedupe_key(doc.organization_id, ex.vendor_name, ex.invoice_number)
        return (
            bool(key)
            and ExtractedData.objects.filter(dedupe_key=key)
            .exclude(document_id=doc.id)
            .exclude(document__status=Document.Status.FAILED)
            .exists()
        )

    return is_duplicate


def _vendor_lookup(doc: Document):
    from apps.documents.vendors import match_vendor, vendor_record

    def lookup(ex):
        return vendor_record(match_vendor(doc.organization_id, ex.vendor_name, ex.vendor_tax_id), ex.vendor_tax_id)

    return lookup


def _new_vendor_checker(doc: Document):
    """None when the organization has not enabled the rule."""
    if not doc.organization.review_new_vendors:
        return None

    def is_new_vendor(ex) -> bool:
        from apps.documents.vendors import match_vendor

        if match_vendor(doc.organization_id, ex.vendor_name, ex.vendor_tax_id):
            return False
        key = dedupe_key(doc.organization_id, ex.vendor_name, "x")
        prefix = key[: key.rfind(":") + 1]
        return not (
            ExtractedData.objects.filter(dedupe_key__startswith=prefix, document__status=Document.Status.APPROVED)
            .exclude(document_id=doc.id)
            .exists()
        )

    return is_new_vendor


def _vendor_history(doc: Document):
    def history(ex):
        key = dedupe_key(doc.organization_id, ex.vendor_name, "x")
        prefix = key[: key.rfind(":") + 1]
        approved = ExtractedData.objects.filter(
            dedupe_key__startswith=prefix, document__status=Document.Status.APPROVED, total_amount__gt=0,
        ).exclude(document_id=doc.id)
        totals = sorted(approved.order_by("-created_at").values_list("total_amount", flat=True)[:50])
        if not totals:
            return None
        currencies = frozenset(c for c in approved.values_list("currency", flat=True).distinct() if c)
        return VendorHistory(count=len(totals), currencies=currencies, median_total=totals[len(totals) // 2])

    return history


def _validation_options(doc: Document) -> dict:
    org = doc.organization
    return {
        "is_duplicate": _duplicate_checker(doc),
        "max_amount": org.auto_approve_max_amount,
        "is_new_vendor": _new_vendor_checker(doc),
        "vendor_history": _vendor_history(doc),
        "own_names": (org.name, *org.other_names),
        "vendor_lookup": _vendor_lookup(doc),
    }


def _claim(document_id: int, task_id: str, force: bool) -> bool:
    """
    Take exclusive ownership of the document for this task.

    A new run may start from PENDING or FAILED. A run already in PROCESSING can only be
    continued by the same task id (a Celery retry or a redelivery keeps its id), so a
    second task never processes the same document at the same time.
    """
    allowed = Q(status__in=[Document.Status.PENDING, Document.Status.FAILED]) | Q(
        status=Document.Status.PROCESSING, processing_task_id=task_id
    )
    if force:
        allowed |= Q(status__in=[Document.Status.REQUIRES_REVIEW, Document.Status.PROCESSED])
    return bool(
        Document.objects.filter(allowed, id=document_id).update(
            status=Document.Status.PROCESSING,
            processing_task_id=task_id,
            processing_started_at=timezone.now(),
            error_message="",
        )
    )


@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True, soft_time_limit=300, time_limit=360)
def process_document(self, document_id: int, force: bool = False):
    """
    Extract, validate and route one document. Safe to run more than once: only the
    task that claimed the document writes results, and they are written in place.
    Approved documents are never reprocessed, so human corrections are never lost.
    """
    task_id = self.request.id or f"direct-{document_id}"
    if not _claim(document_id, task_id, force):
        return f"document {document_id} skipped (missing, approved, or owned by another run)"
    doc = Document.objects.select_related("organization").get(id=document_id)

    try:
        doc_input = load_document(doc.file)
        result = run_pipeline(
            doc_input,
            provider=get_provider(),
            threshold=doc.organization.auto_approve_threshold,
            **_validation_options(doc),
            escalation_errors=(ProcessingError, SoftTimeLimitExceeded),
        )
        saved = _save_result(doc, task_id, doc_input, result)
    except PermanentProcessingError as exc:
        logger.warning("document %s failed: %s", document_id, exc)
        _mark_failed(document_id, task_id, str(exc))
        return f"document {document_id} failed"
    except (TransientProcessingError, SoftTimeLimitExceeded) as exc:
        if self.request.retries >= self.max_retries:
            _mark_failed(document_id, task_id, f"Gave up after {self.max_retries} retries: {exc}")
            return f"document {document_id} failed"
        raise self.retry(exc=exc, countdown=min(300, 15 * 2 ** self.request.retries))
    except Exception:
        # Never leave a document stuck in PROCESSING. Details go to the log, not to the user.
        logger.exception("document %s failed with an unexpected error", document_id)
        _mark_failed(document_id, task_id, GENERIC_ERROR)
        return f"document {document_id} failed"

    if not saved:
        return f"document {document_id} result discarded (another run took over)"
    return f"document {document_id} {result.decision}"


def _save_result(doc: Document, task_id: str, doc_input, result) -> bool:
    ex = result.extraction
    now = timezone.now()
    org = doc.organization
    with transaction.atomic():
        # One writer per organization at a time: two copies of the same invoice that are
        # processed together cannot both pass the duplicate check and both auto-approve.
        Organization.objects.select_for_update().filter(id=org.id).first()
        locked = Document.objects.select_for_update().filter(id=doc.id, processing_task_id=task_id).first()
        if locked is None or locked.status != Document.Status.PROCESSING:
            return False

        # Validate again under the lock: the duplicate check must see rows saved since the pipeline ran.
        report = validate(ex, **_validation_options(doc))
        decision = AUTO_APPROVE if (result.decision == AUTO_APPROVE and report.can_auto_approve(
            org.auto_approve_threshold)) else REVIEW

        ExtractedData.objects.update_or_create(
            document=doc,
            defaults={
                "raw_extraction": ex.to_json(),
                "invoice_number": ex.invoice_number or "",
                "invoice_date": ex.invoice_date,
                "due_date": ex.due_date,
                "vendor_name": ex.vendor_name or "",
                "vendor_tax_id": ex.vendor_tax_id or "",
                "vendor_id": getattr(_vendor_lookup(doc)(ex), "id", None),
                "customer_name": ex.customer_name or "",
                "purchase_order": ex.purchase_order or "",
                "subtotal": _storable(ex.subtotal),
                "tax_amount": _storable(ex.tax_amount),
                "total_amount": _storable(ex.total_amount),
                "amount_due": _storable(ex.amount_due),
                "bank_account": ex.bank_account or "",
                "bank_code": ex.bank_code or "",
                "currency": ex.currency or "",
                "line_items": [li.model_dump(mode="json") for li in ex.line_items],
                "overall_confidence": report.score,
                "field_confidences": report.field_confidences(ex),
                "validation": report.to_json(),
                "dedupe_key": dedupe_key(doc.organization_id, ex.vendor_name, ex.invoice_number),
            },
        )
        locked.doc_type = "invoice" if ex.is_invoice else "other"
        locked.ocr_text = doc_input.text or ""
        locked.page_count = doc_input.page_count
        locked.processing_meta = result.meta(doc_input) | {"decision": decision}
        locked.error_message = ""
        if decision == AUTO_APPROVE:
            locked.status = Document.Status.APPROVED
            locked.approved_at = now
            ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).update(
                status=ReviewTask.STATUS_APPROVED, reviewed_at=now
            )
        else:
            locked.status = Document.Status.REQUIRES_REVIEW
            locked.approved_at = None
            if not ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).exists():
                ReviewTask.objects.create(document=doc)
        locked.save()
        _emit(doc.id, "invoice.approved" if decision == AUTO_APPROVE else "invoice.needs_review")
    return True


@shared_task
def fail_stale_documents():
    """Mark runs that died without a result as FAILED. Schedule with Celery beat (every 5 minutes)."""
    cutoff = timezone.now() - STALE_AFTER
    count = Document.objects.filter(status=Document.Status.PROCESSING, processing_started_at__lt=cutoff).update(
        status=Document.Status.FAILED,
        error_message="Processing did not finish in time. Use Reprocess to try again.",
        updated_at=timezone.now(),
    )
    return f"{count} stale documents marked failed"


@shared_task(bind=True, max_retries=None, acks_late=True)
def deliver_webhook(self, log_id: int):
    """Deliver one webhook event; retry on the RETRY_SCHEDULE (about 21 hours), then mark it failed."""
    from apps.documents.models import WebhookDeliveryLog
    from apps.documents.webhooks import RETRY_SCHEDULE, attempt

    log = WebhookDeliveryLog.objects.select_related("webhook_config").filter(id=log_id).first()
    if log is None or log.status != WebhookDeliveryLog.Status.PENDING:
        return f"webhook {log_id} skipped"
    if not log.webhook_config.is_active:
        WebhookDeliveryLog.objects.filter(id=log_id).update(status=WebhookDeliveryLog.Status.FAILED,
                                                            last_error="The endpoint was turned off")
        return f"webhook {log_id} endpoint off"
    if attempt(log):
        return f"webhook {log_id} delivered"
    retry = log.attempts - 1
    if retry >= len(RETRY_SCHEDULE):
        WebhookDeliveryLog.objects.filter(id=log_id).update(status=WebhookDeliveryLog.Status.FAILED, next_attempt_at=None)
        return f"webhook {log_id} failed after {log.attempts} attempts"
    countdown = RETRY_SCHEDULE[retry]
    WebhookDeliveryLog.objects.filter(id=log_id).update(next_attempt_at=timezone.now() + timedelta(seconds=countdown))
    raise self.retry(countdown=countdown)


def _emit(document_id: int, event_type: str) -> None:
    """Webhook events never break processing: a failure here is logged, not raised."""
    from apps.documents.webhooks import emit

    try:
        emit(Document.objects.select_related("extracted_data").get(id=document_id), event_type)
    except Exception:  # noqa: BLE001
        logger.exception("could not record webhook event %s for document %s", event_type, document_id)
