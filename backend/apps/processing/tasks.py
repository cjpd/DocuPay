from celery import shared_task


@shared_task
def process_document(document_id: int):
    """
    Orchestrate OCR -> classify -> extract -> confidence scoring -> review creation (placeholder).
    """
    return f"processed {document_id}"


@shared_task
def send_webhook(document_id: int):
    """
    Deliver extraction results to configured webhook (placeholder).
    """
    return f"sent webhook for {document_id}"
