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
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        user = self.request.user
        return Document.objects.filter(organization__memberships__user=user).distinct()

    def perform_create(self, serializer):
        org = OrgMembership.objects.filter(user=self.request.user).values_list("organization", flat=True).first()
        if not org:
            raise permissions.PermissionDenied("User is not a member of any organization")
        doc = serializer.save(uploaded_by=self.request.user, organization_id=org, status=Document.Status.PROCESSING)
        process_document.delay(doc.id)

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request, *args, **kwargs):
        """
        Multipart upload endpoint to create a document and queue processing.
        """
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        org = OrgMembership.objects.filter(user=request.user).values_list("organization", flat=True).first()
        if not org:
            return Response({"detail": "No organization membership"}, status=status.HTTP_403_FORBIDDEN)

        document = Document.objects.create(
            organization_id=org,
            uploaded_by=request.user,
            file=file,
            status=Document.Status.PROCESSING,
        )
        process_document.delay(document.id)
        serializer = self.get_serializer(document)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExtractedDataViewSet(viewsets.ModelViewSet):
    serializer_class = ExtractedDataSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        user = self.request.user
        return ExtractedData.objects.filter(document__organization__memberships__user=user).distinct()


class ReviewTaskViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewTaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        user = self.request.user
        return ReviewTask.objects.filter(document__organization__memberships__user=user).distinct()

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
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        user = self.request.user
        return WebhookConfig.objects.filter(organization__memberships__user=user).distinct()


class WebhookDeliveryLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = WebhookDeliveryLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        user = self.request.user
        return WebhookDeliveryLog.objects.filter(document__organization__memberships__user=user).distinct()
