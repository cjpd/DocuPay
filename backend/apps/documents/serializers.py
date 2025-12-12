from rest_framework import serializers

from .models import Document, ExtractedData, ReviewTask, WebhookConfig, WebhookDeliveryLog


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "organization", "uploaded_by", "file", "status", "doc_type", "ocr_text", "created_at", "updated_at"]
        read_only_fields = ["uploaded_by", "status", "doc_type", "ocr_text", "created_at", "updated_at"]


class ExtractedDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractedData
        fields = ["id", "document", "doc_type", "data", "raw_model_response", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class ReviewTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = ["id", "document", "assigned_to", "status", "created_at", "completed_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class WebhookConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookConfig
        fields = ["id", "organization", "target_url", "secret", "is_active", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class WebhookDeliveryLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDeliveryLog
        fields = ["id", "webhook_config", "document", "status_code", "success", "attempts", "last_error", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]
