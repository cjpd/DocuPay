from rest_framework import serializers

from .models import (
    CorrectionExample,
    Document,
    ExtractedData,
    ReviewTask,
    WebhookConfig,
    WebhookDeliveryLog,
)


class ExtractedDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractedData
        fields = [
            "id",
            "document",
            "raw_extraction",
            "invoice_number",
            "invoice_date",
            "due_date",
            "vendor_name",
            "total_amount",
            "currency",
            "line_items",
            "overall_confidence",
            "field_confidences",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "document", "created_at", "updated_at"]


class DocumentSerializer(serializers.ModelSerializer):
    confidence = serializers.SerializerMethodField()
    extracted_data = ExtractedDataSerializer(read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "organization",
            "uploaded_by",
            "file",
            "status",
            "doc_type",
            "ocr_text",
            "confidence",
            "extracted_data",
            "approved_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uploaded_by",
            "status",
            "doc_type",
            "ocr_text",
            "confidence",
            "extracted_data",
            "approved_at",
            "created_at",
            "updated_at",
        ]

    def get_confidence(self, obj):
        extracted = getattr(obj, "extracted_data", None)
        if extracted:
            return extracted.overall_confidence
        return None


class ReviewTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = [
            "id",
            "document",
            "assigned_to",
            "status",
            "reviewed_by",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "document",
            "created_at",
            "updated_at",
            "reviewed_by",
            "reviewed_at",
        ]


class CorrectionExampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorrectionExample
        fields = ["id", "document", "corrected_fields", "raw_extraction", "created_at", "updated_at"]
        read_only_fields = ["id", "document", "created_at", "updated_at"]


class WebhookConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookConfig
        fields = ["id", "organization", "target_url", "secret", "is_active", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class WebhookDeliveryLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDeliveryLog
        fields = [
            "id",
            "webhook_config",
            "document",
            "status_code",
            "success",
            "attempts",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
