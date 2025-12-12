from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.permissions import IsOrgMember
from apps.organizations.models import OrgMembership
from .models import Document, ExtractedData, ReviewTask, WebhookConfig, WebhookDeliveryLog
from .serializers import (
    DocumentSerializer,
    ExtractedDataSerializer,
    ReviewTaskSerializer,
    WebhookConfigSerializer,
    WebhookDeliveryLogSerializer,
)


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
        serializer.save(uploaded_by=self.request.user, organization_id=org, status="queued")

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
            status="queued",
        )
        # TODO: enqueue Celery task to process document
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
        task.status = "approved"
        task.save(update_fields=["status"])
        task.document.status = "processed"
        task.document.save(update_fields=["status"])
        return Response({"status": "approved"})

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        task = self.get_object()
        task.status = "rejected"
        task.save(update_fields=["status"])
        task.document.status = "needs_review"
        task.document.save(update_fields=["status"])
        return Response({"status": "rejected"})


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
