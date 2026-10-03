import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from apps.documents.models import Document, ExtractedData, ReviewTask

from .errors import PermanentProcessingError, TransientProcessingError
from .ingest import load_document
from .pipeline import AUTO_APPROVE, run_pipeline
from .providers import get_provider
from .validation import ValidationReport

logger = logging.getLogger(__name__)

# A document in one of these states is (re)processed. Others are skipped unless force=True.
CLAIMABLE = (Document.Status.PENDING, Document.Status.PROCESSING, Document.Status.FAILED)
MAX_RETRIES = 5


def _mark_failed(document_id: int, message: str) -> None:
    Document.objects.filter(id=document_id).update(
        status=Document.Status.FAILED, error_message=message[:2000], updated_at=timezone.now()
    )


def _duplicate_checker(doc: Document):
    def is_duplicate(ex) -> bool:
        return (
            ExtractedData.objects.filter(
                document__organization_id=doc.organization_id,
                vendor_name__iexact=ex.vendor_name,
                invoice_number__iexact=ex.invoice_number,
            )
            .exclude(document_id=doc.id)
            .exclude(document__status=Document.Status.FAILED)
            .exists()
        )

    return is_duplicate


@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True, soft_time_limit=240, time_limit=300)
def process_document(self, document_id: int, force: bool = False):
    """
    Extract, validate and route one document. Safe to run more than once:
    results are written with update_or_create and a finished document is skipped.
    """
    statuses = None if force else CLAIMABLE
    claimed = Document.objects.filter(id=document_id)
    if statuses:
        claimed = claimed.filter(status__in=statuses)
    if not claimed.update(status=Document.Status.PROCESSING, error_message=""):
        return f"document {document_id} skipped (missing or already processed)"
    doc = Document.objects.select_related("organization").get(id=document_id)

    try:
        doc_input = load_document(doc.file)
        result = run_pipeline(
            doc_input,
            provider=get_provider(),
            threshold=doc.organization.auto_approve_threshold,
            is_duplicate=_duplicate_checker(doc),
        )
    except PermanentProcessingError as exc:
        logger.warning("document %s failed: %s", document_id, exc)
        _mark_failed(document_id, str(exc))
        return f"document {document_id} failed"
    except (TransientProcessingError, SoftTimeLimitExceeded) as exc:
        if self.request.retries >= self.max_retries:
            _mark_failed(document_id, f"Gave up after {self.max_retries} retries: {exc}")
            return f"document {document_id} failed"
        raise self.retry(exc=exc, countdown=min(300, 15 * 2 ** self.request.retries))

    _save_result(doc, doc_input, result)
    return f"document {document_id} {result.decision}"


def _save_result(doc: Document, doc_input, result) -> None:
    ex = result.extraction
    report: ValidationReport = result.report
    now = timezone.now()
    with transaction.atomic():
        ExtractedData.objects.update_or_create(
            document=doc,
            defaults={
                "raw_extraction": ex.to_json(),
                "invoice_number": ex.invoice_number or "",
                "invoice_date": ex.invoice_date,
                "due_date": ex.due_date,
                "vendor_name": ex.vendor_name or "",
                "customer_name": ex.customer_name or "",
                "purchase_order": ex.purchase_order or "",
                "subtotal": ex.subtotal,
                "tax_amount": ex.tax_amount,
                "total_amount": ex.total_amount,
                "currency": ex.currency or "",
                "line_items": [li.model_dump(mode="json") for li in ex.line_items],
                "overall_confidence": report.score,
                "field_confidences": report.field_confidences(ex),
                "validation": report.to_json(),
            },
        )
        doc.doc_type = "invoice" if ex.is_invoice else "other"
        doc.ocr_text = doc_input.text or ""
        doc.page_count = doc_input.page_count
        doc.processing_meta = result.meta(doc_input)
        doc.error_message = ""
        if result.decision == AUTO_APPROVE:
            doc.status = Document.Status.APPROVED
            doc.approved_at = now
            ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).update(
                status=ReviewTask.STATUS_APPROVED, reviewed_at=now
            )
        else:
            doc.status = Document.Status.REQUIRES_REVIEW
            doc.approved_at = None
            if not ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).exists():
                ReviewTask.objects.create(document=doc)
        doc.save()


@shared_task
def send_webhook(document_id: int):
    """
    Deliver extraction results to configured webhook (placeholder, see DP-16).
    """
    return f"sent webhook for {document_id}"
