from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.models import OrgMembership
from apps.organizations.permissions import IsOrgMember
from .models import CorrectionExample, Document, ExtractedData, ReviewTask, WebhookConfig, WebhookDeliveryLog
from .serializers import (
    CorrectionExampleSerializer,
    DocumentSerializer,
    ExtractedDataSerializer,
    ReviewTaskSerializer,
    WebhookConfigSerializer,
    WebhookDeliveryLogSerializer,
)
from apps.processing.tasks import process_document


class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    permission_classes = [permissions.AllowAny]

    def _default_org(self):
        from apps.organizations.models import Organization
        org, _ = Organization.objects.get_or_create(name="Default Org", defaults={"slug": "default-org"})
        return org

    def get_queryset(self):
        user = self.request.user
        if user and user.is_authenticated:
            return Document.objects.filter(organization__memberships__user=user).distinct()
        return Document.objects.all()

    def perform_create(self, serializer):
        user = self.request.user
        if user and user.is_authenticated:
            org = OrgMembership.objects.filter(user=user).values_list("organization", flat=True).first()
            org_id = org or self._default_org().id
        else:
            org_id = self._default_org().id
        doc = serializer.save(uploaded_by=None, organization_id=org_id, status=Document.Status.PROCESSING)
        process_document.delay(doc.id)

    @action(detail=False, methods=["get"], url_path="analytics")
    def analytics(self, request):
        now = timezone.now()
        window = now - timedelta(hours=24)
        prev_window = now - timedelta(hours=48)

        org_docs = self.get_queryset()
        processed_statuses = [Document.Status.APPROVED, Document.Status.PROCESSED]

        processed_24h = org_docs.filter(status__in=processed_statuses, updated_at__gte=window).count()
        processed_prev = org_docs.filter(
            status__in=processed_statuses, updated_at__gte=prev_window, updated_at__lt=window
        ).count()

        if processed_prev:
            delta = round((processed_24h - processed_prev) / processed_prev * 100, 1)
        elif processed_24h:
            delta = 100.0
        else:
            delta = 0.0

        pending_review = ReviewTask.objects.filter(
            document__in=org_docs, status=ReviewTask.STATUS_PENDING
        ).count()

        avg_conf_raw = (
            ExtractedData.objects.filter(document__in=org_docs)
            .aggregate(avg=Avg("overall_confidence"))["avg"]
            or 0.0
        )

        webhook_stats = WebhookDeliveryLog.objects.filter(document__in=org_docs).aggregate(
            total=Count("id"), successful=Count("id", filter=Q(success=True))
        )
        if webhook_stats["total"]:
            webhook_success = round(webhook_stats["successful"] / webhook_stats["total"] * 100, 1)
        else:
            webhook_success = None

        return Response(
            {
                "docs_processed_24h": processed_24h,
                "docs_processed_24h_delta": delta,
                "pending_review": pending_review,
                "avg_confidence": round(avg_conf_raw * 100, 1),
                "webhook_success_rate": webhook_success,
            }
        )

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request, *args, **kwargs):
        """
        Multipart upload endpoint to create a document and queue processing.
        """
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user if request.user and request.user.is_authenticated else None
        org = (
            OrgMembership.objects.filter(user=user).values_list("organization", flat=True).first()
            if user else None
        ) or self._default_org().id

        document = Document.objects.create(
            organization_id=org,
            uploaded_by=user,
            file=file,
            status=Document.Status.PROCESSING,
        )
        process_document.delay(document.id)
        serializer = self.get_serializer(document)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExtractedDataViewSet(viewsets.ModelViewSet):
    serializer_class = ExtractedDataSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        if user and user.is_authenticated:
            return ExtractedData.objects.filter(document__organization__memberships__user=user).distinct()
        return ExtractedData.objects.all()


class ReviewTaskViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewTaskSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        if user and user.is_authenticated:
            return ReviewTask.objects.filter(document__organization__memberships__user=user).distinct()
        return ReviewTask.objects.all()

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        task = self.get_object()
        if task.status != ReviewTask.STATUS_PENDING:
            return Response({"detail": "Task is not pending"}, status=status.HTTP_400_BAD_REQUEST)
        corrections = request.data.get("corrections", {})
        if not isinstance(corrections, dict):
            return Response({"detail": "corrections must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        extracted = getattr(task.document, "extracted_data", None)
        if not extracted:
            extracted = ExtractedData.objects.create(document=task.document, raw_extraction={})

        # apply corrections
        for field, value in corrections.items():
            if hasattr(extracted, field):
                setattr(extracted, field, value)
        extracted.save()

        # store correction example
        CorrectionExample.objects.create(
            document=task.document,
            corrected_fields=corrections,
            raw_extraction=extracted.raw_extraction,
        )

        task.status = ReviewTask.STATUS_APPROVED
        task.reviewed_by = request.user
        task.reviewed_at = timezone.now()
        task.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        task.document.status = Document.Status.APPROVED
        task.document.approved_at = timezone.now()
        task.document.save(update_fields=["status", "approved_at"])
        return Response(ReviewTaskSerializer(task).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        task = self.get_object()
        if task.status != ReviewTask.STATUS_PENDING:
            return Response({"detail": "Task is not pending"}, status=status.HTTP_400_BAD_REQUEST)
        task.status = ReviewTask.STATUS_REJECTED
        task.reviewed_by = request.user
        task.reviewed_at = timezone.now()
        task.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        task.document.status = Document.Status.REQUIRES_REVIEW
        task.document.save(update_fields=["status"])
        return Response(ReviewTaskSerializer(task).data)


class WebhookConfigViewSet(viewsets.ModelViewSet):
    serializer_class = WebhookConfigSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        return WebhookConfig.objects.filter(organization__memberships__user=user).distinct()


class WebhookDeliveryLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = WebhookDeliveryLogSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        return WebhookDeliveryLog.objects.filter(document__organization__memberships__user=user).distinct()
