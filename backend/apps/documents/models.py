import uuid

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


class Vendor(TimeStampedModel):
    """
    A company that sends invoices to the organization (accounts payable master data).

    Vendors are learned when a person approves an invoice, and can be edited on the Vendors page.
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="vendors")
    name = models.CharField(max_length=255)
    # normalize_vendor(name): "ACME Supplies, LLC" -> "acmesupplies". Unique per organization.
    name_key = models.CharField(max_length=255)
    # Other names the vendor uses on invoices, matched the same way as the name.
    aliases = models.JSONField(default=list, blank=True)
    tax_id = models.CharField(max_length=64, blank=True, default="")
    default_currency = models.CharField(max_length=3, blank=True, default="")
    # Where this vendor is paid. Set from the first approved invoice, then changed only by an
    # owner or admin on the Vendors page (after confirming the change with the vendor).
    bank_account = models.CharField(max_length=64, blank=True, default="")
    bank_code = models.CharField(max_length=32, blank=True, default="")
    # A blocked vendor's invoices always go to a person (fraud, disputes, closed accounts).
    is_blocked = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "name_key"], name="vendor_unique_name_per_org")]
        indexes = [models.Index(fields=["organization", "tax_id"])]
        ordering = ["name"]

    def save(self, *args, **kwargs):
        from apps.processing.normalize import normalize_tax_id, normalize_vendor

        self.name_key = normalize_vendor(self.name)
        from apps.processing.normalize import normalize_bank

        self.tax_id = normalize_tax_id(self.tax_id)
        self.bank_account = normalize_bank(self.bank_account)
        self.bank_code = normalize_bank(self.bank_code)
        self.default_currency = (self.default_currency or "").upper()[:3]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class ExtractedData(TimeStampedModel):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="extracted_data")
    raw_extraction = models.JSONField(default=dict)
    invoice_number = models.CharField(max_length=128, blank=True, default="")
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    vendor_name = models.CharField(max_length=255, blank=True, default="")
    vendor_tax_id = models.CharField(max_length=64, blank=True, default="")
    # The matched vendor from the vendor list, if any.
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    purchase_order = models.CharField(max_length=128, blank=True, default="")
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount_due = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    bank_account = models.CharField(max_length=64, blank=True, default="")
    bank_code = models.CharField(max_length=32, blank=True, default="")
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
        "invoice_number", "invoice_date", "due_date", "vendor_name", "vendor_tax_id", "customer_name", "purchase_order",
        "subtotal", "tax_amount", "total_amount", "amount_due", "currency", "line_items", "bank_account", "bank_code",
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


def _all_events():
    return ["invoice.approved", "invoice.needs_review", "invoice.rejected", "invoice.failed"]


class WebhookConfig(TimeStampedModel):
    """A customer endpoint that receives invoice events (see apps.documents.webhooks)."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="webhook_configs")
    target_url = models.URLField(max_length=500)
    description = models.CharField(max_length=200, blank=True, default="")
    # Signing secret (HMAC-SHA256). Generated by DocuPay and shown once.
    secret = models.CharField(max_length=255)
    events = models.JSONField(default=_all_events, blank=True)
    is_active = models.BooleanField(default=True)


class WebhookDeliveryLog(TimeStampedModel):
    """One event for one endpoint, with every delivery attempt summarized."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    webhook_config = models.ForeignKey(WebhookConfig, on_delete=models.CASCADE, related_name="delivery_logs")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="webhook_delivery_logs")
    # The same id on every retry, so the receiver can ignore repeats (no second bill).
    event_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    event_type = models.CharField(max_length=40, default="")
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_code = models.IntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)
    response_ms = models.IntegerField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)


class CorrectionExample(TimeStampedModel):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="correction_examples")
    corrected_fields = models.JSONField(default=dict)
    raw_extraction = models.JSONField(default=dict)
