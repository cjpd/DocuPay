from django.db import models

from apps.common.models import TimeStampedModel


class Document(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        REQUIRES_REVIEW = "requires_review", "Requires Review"
        APPROVED = "approved", "Approved"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, related_name="uploaded_documents"
    )
    file = models.FileField(upload_to="documents/")
    status = models.CharField(max_length=50, choices=Status.choices, default=Status.PENDING)
    doc_type = models.CharField(max_length=50, blank=True)
    ocr_text = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    # Models used, tokens, estimated cost and escalation path for each processing run.
    processing_meta = models.JSONField(default=dict, blank=True)
    # The Celery task that owns the current run. Only that task may write results.
    processing_task_id = models.CharField(max_length=64, blank=True, default="")
    processing_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.doc_type or 'document'} #{self.pk}"


class ExtractedData(TimeStampedModel):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="extracted_data")
    raw_extraction = models.JSONField(default=dict)
    invoice_number = models.CharField(max_length=128, blank=True, default="")
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    vendor_name = models.CharField(max_length=255, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    purchase_order = models.CharField(max_length=128, blank=True, default="")
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, blank=True, default="")
    line_items = models.JSONField(default=list, blank=True)
    overall_confidence = models.FloatField(default=0.0)
    field_confidences = models.JSONField(default=dict, blank=True)
    # Result of each validation check (see apps.processing.validation).
    validation = models.JSONField(default=list, blank=True)
    # "<org id>:<normalized vendor>:<normalized invoice number>" for duplicate detection.
    dedupe_key = models.CharField(max_length=400, blank=True, default="", db_index=True)

    # Fields a reviewer may correct. Anything else in a correction is rejected.
    EDITABLE_FIELDS = (
        "invoice_number", "invoice_date", "due_date", "vendor_name", "customer_name", "purchase_order",
        "subtotal", "tax_amount", "total_amount", "currency", "line_items",
    )


class ReviewTask(TimeStampedModel):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    )

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="review_tasks")
    assigned_to = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="review_tasks"
    )
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by = models.ForeignKey(
        "users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_tasks"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)


class WebhookConfig(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="webhook_configs")
    target_url = models.URLField()
    secret = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)


class WebhookDeliveryLog(TimeStampedModel):
    webhook_config = models.ForeignKey(WebhookConfig, on_delete=models.CASCADE, related_name="delivery_logs")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="webhook_delivery_logs")
    status_code = models.IntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)


class CorrectionExample(TimeStampedModel):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="correction_examples")
    corrected_fields = models.JSONField(default=dict)
    raw_extraction = models.JSONField(default=dict)
