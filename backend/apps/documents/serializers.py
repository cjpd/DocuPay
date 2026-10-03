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
            "customer_name",
            "purchase_order",
            "subtotal",
            "tax_amount",
            "total_amount",
            "currency",
            "line_items",
            "overall_confidence",
            "field_confidences",
            "validation",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "document", "raw_extraction", "overall_confidence", "field_confidences", "validation",
            "created_at", "updated_at",
        ]


class DocumentSerializer(serializers.ModelSerializer):
    confidence = serializers.SerializerMethodField()
    extracted_data = ExtractedDataSerializer(read_only=True)
    file_name = serializers.SerializerMethodField()
    review_task_id = serializers.SerializerMethodField()
    # Upload only. The file is downloaded through /documents/<id>/file/, which checks access;
    # a direct storage URL would bypass tenant checks.
    file = serializers.FileField(write_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "organization",
            "uploaded_by",
            "file",
            "file_name",
            "review_task_id",
            "status",
            "doc_type",
            "ocr_text",
            "confidence",
            "extracted_data",
            "approved_at",
            "page_count",
            "error_message",
            "processing_meta",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "organization",
            "page_count",
            "error_message",
            "processing_meta",
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

    def get_file_name(self, obj):
        return obj.file.name.rsplit("/", 1)[-1] if obj.file else ""

    def get_review_task_id(self, obj):
        """The open review task, so the UI can link straight to it."""
        tasks = [t for t in obj.review_tasks.all() if t.status == ReviewTask.STATUS_PENDING]
        return tasks[0].id if tasks else None

    def get_confidence(self, obj):
        extracted = getattr(obj, "extracted_data", None)
        if extracted:
            return extracted.overall_confidence
        return None


class ReviewTaskSerializer(serializers.ModelSerializer):
    document_detail = DocumentSerializer(source="document", read_only=True)

    class Meta:
        model = ReviewTask
        fields = [
            "id",
            "document",
            "document_detail",
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
        read_only_fields = ["organization", "created_at", "updated_at"]
        # The signing secret is set by the customer and never sent back.
        extra_kwargs = {"secret": {"write_only": True}}


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
