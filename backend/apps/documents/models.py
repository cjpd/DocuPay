from django.db import models

from apps.common.models import TimeStampedModel


class Document(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey("users.CustomUser", on_delete=models.SET_NULL, null=True, related_name="uploaded_documents")
    file = models.FileField(upload_to="documents/")
    status = models.CharField(max_length=50, default="pending")
    doc_type = models.CharField(max_length=50, blank=True)
    ocr_text = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.doc_type or 'document'} #{self.pk}"


class ExtractedData(TimeStampedModel):
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="extracted_data")
    doc_type = models.CharField(max_length=50, blank=True)
    data = models.JSONField(default=dict)
    raw_model_response = models.JSONField(default=dict)


class ReviewTask(TimeStampedModel):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="review_tasks")
    assigned_to = models.ForeignKey("users.CustomUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="review_tasks")
    status = models.CharField(max_length=50, default="pending")
    completed_at = models.DateTimeField(null=True, blank=True)


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
