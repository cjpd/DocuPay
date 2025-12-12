import logging
from celery import shared_task
from django.utils import timezone

from apps.documents.models import Document, ExtractedData, ReviewTask
from apps.processing.ocr import run_ocr
from apps.processing.extraction import extract_fields


@shared_task
def process_document(document_id: int):
    """
    Stub pipeline with confidence-based auto-approval.
    """
    try:
        doc = Document.objects.select_related("organization").get(id=document_id)
    except Document.DoesNotExist:
        return f"document {document_id} missing"

    if not doc.ocr_text:
        try:
            file_path = doc.file.path if hasattr(doc.file, "path") else ""
        except Exception:
            file_path = ""
        doc.ocr_text = run_ocr(file_path)

    extraction = extract_fields(doc.doc_type, doc.ocr_text or "")
    doc.doc_type = doc.doc_type or "invoice"
    doc.save(update_fields=["ocr_text", "doc_type"])

    extracted, _ = ExtractedData.objects.update_or_create(
        document=doc,
        defaults={
            "raw_extraction": extraction.get("raw_extraction", {}),
            "invoice_number": extraction.get("invoice_number", ""),
            "invoice_date": extraction.get("invoice_date") or None,
            "due_date": extraction.get("due_date") or None,
            "vendor_name": extraction.get("vendor_name", ""),
            "total_amount": extraction.get("total_amount") or None,
            "currency": extraction.get("currency", ""),
            "line_items": extraction.get("line_items", []),
            "overall_confidence": extraction.get("overall_confidence", 0.0),
            "field_confidences": extraction.get("field_confidences", {}),
        },
    )

    threshold = doc.organization.auto_approve_threshold if doc.organization else 0.92
    if extracted.overall_confidence >= threshold:
        doc.status = Document.Status.APPROVED
        doc.approved_at = timezone.now()
        doc.save(update_fields=["status", "approved_at"])
        ReviewTask.objects.filter(document=doc, status=ReviewTask.STATUS_PENDING).update(
            status=ReviewTask.STATUS_APPROVED, reviewed_at=timezone.now()
        )
    else:
        doc.status = Document.Status.REQUIRES_REVIEW
        doc.save(update_fields=["status"])
        ReviewTask.objects.get_or_create(document=doc, status=ReviewTask.STATUS_PENDING)

    return f"processed {document_id}"


@shared_task
def send_webhook(document_id: int):
    """
    Deliver extraction results to configured webhook (placeholder).
    """
    return f"sent webhook for {document_id}"
