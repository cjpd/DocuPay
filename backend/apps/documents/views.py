from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.permissions import IsOrgMember
from apps.organizations.scoping import OrgScopedMixin, active_organization_id
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


class DocumentViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]
    # No PUT/PATCH: a document's file and organization never change after upload.
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return (
            self.scope(Document.objects.all())
            .select_related("extracted_data")
            .prefetch_related("review_tasks")
            .order_by("-created_at")
        )

    @action(detail=True, methods=["get"], url_path="file")
    def file(self, request, *args, **kwargs):
        """The original file, for the review screen. Access is checked like any other document read."""
        import io

        from PIL import Image

        from apps.processing.ingest import read_file_bytes, sniff

        document = self.get_object()
        data = read_file_bytes(document.file)
        kind = sniff(data)
        content_type = {"pdf": "application/pdf", "text": "text/plain; charset=utf-8"}.get(kind)
        if kind == "image":
            try:
                content_type = Image.MIME.get(Image.open(io.BytesIO(data)).format, "application/octet-stream")
            except Exception:
                content_type = "application/octet-stream"
        response = FileResponse(io.BytesIO(data), content_type=content_type or "application/octet-stream")
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"  # uploaded files never run scripts
        return response

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request, *args, **kwargs):
        """Dashboard numbers, computed from real documents (last `days` days, default 30)."""
        try:
            days = max(1, min(365, int(request.query_params.get("days", 30))))
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)
        docs = self.scope(Document.objects.filter(created_at__gte=since)).select_related("extracted_data")
        by_status = defaultdict(int)
        auto, by_person = 0, 0
        approved_value = defaultdict(Decimal)
        cost = Decimal("0")
        per_day = defaultdict(lambda: {"received": 0, "auto_approved": 0})
        for doc in docs:
            by_status[doc.status] += 1
            meta = doc.processing_meta or {}
            day = doc.created_at.date().isoformat()
            per_day[day]["received"] += 1
            if doc.status == Document.Status.APPROVED:
                if meta.get("decision") == "auto_approve":
                    auto += 1
                    per_day[day]["auto_approved"] += 1
                else:
                    by_person += 1
                data = getattr(doc, "extracted_data", None)
                if data and data.total_amount is not None:
                    approved_value[data.currency or "?"] += data.total_amount
            try:
                cost += Decimal(meta.get("total_cost_usd") or "0")
            except InvalidOperation:
                pass
        processed = auto + by_person + by_status[Document.Status.REQUIRES_REVIEW]
        today = timezone.now().date()
        series = [
            {"date": (today - timedelta(days=i)).isoformat(),
             **per_day.get((today - timedelta(days=i)).isoformat(), {"received": 0, "auto_approved": 0})}
            for i in range(min(days, 14) - 1, -1, -1)
        ]
        return Response({
            "days": days,
            "received": sum(by_status.values()),
            "by_status": dict(by_status),
            "auto_approved": auto,
            "approved_by_person": by_person,
            "needs_review": by_status[Document.Status.REQUIRES_REVIEW],
            "failed": by_status[Document.Status.FAILED],
            "in_progress": by_status[Document.Status.PENDING] + by_status[Document.Status.PROCESSING],
            # Share of processed documents that needed no person.
            "straight_through_rate": round(auto / processed, 4) if processed else None,
            "approved_value": {k: str(v) for k, v in sorted(approved_value.items())},
            "processing_cost_usd": str(cost.quantize(Decimal("0.0001"))),
            "series": series,
        })

    def perform_create(self, serializer):
        org = active_organization_id(self.request, require=True)
        doc = serializer.save(uploaded_by=self.request.user, organization_id=org, status=Document.Status.PENDING)
        _queue(doc.id)

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request, *args, **kwargs):
        """
        Multipart upload endpoint to create a document and queue processing.
        """
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        org = active_organization_id(request, require=True)
        document = Document.objects.create(
            organization_id=org,
            uploaded_by=request.user,
            file=file,
            status=Document.Status.PENDING,
        )
        _queue(document.id)
        serializer = self.get_serializer(document)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="reprocess")
    def reprocess(self, request, *args, **kwargs):
        """Run the pipeline again, for example after a FAILED status or a provider change.
        Approved documents cannot be reprocessed: that would overwrite a reviewer's corrections."""
        document = self.get_object()
        reprocessable = [Document.Status.FAILED, Document.Status.REQUIRES_REVIEW, Document.Status.PENDING]
        if not Document.objects.filter(id=document.id, status__in=reprocessable).update(
            status=Document.Status.PENDING, error_message=""
        ):
            return Response(
                {"detail": f"A document with status '{document.status}' cannot be reprocessed."},
                status=status.HTTP_409_CONFLICT,
            )
        _queue(document.id)
        document.refresh_from_db()
        return Response(self.get_serializer(document).data, status=status.HTTP_202_ACCEPTED)


def _queue(document_id: int, force: bool = False) -> None:
    # Queue after commit, so the worker never looks for a row that is not saved yet.
    transaction.on_commit(lambda: process_document.delay(document_id, force=force))


class ExtractedDataViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Read only. Corrections go through reviews/<id>/approve/, which records them."""

    serializer_class = ExtractedDataSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    org_field = "document__organization"

    def get_queryset(self):
        return self.scope(ExtractedData.objects.all()).order_by("-created_at")


class ReviewTaskViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Review tasks change only through approve/reject, which record who decided and what changed."""

    serializer_class = ReviewTaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]
    org_field = "document__organization"

    def get_queryset(self):
        queryset = self.scope(
            ReviewTask.objects.select_related("document", "document__extracted_data")
            .prefetch_related("document__review_tasks")
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by("created_at")

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        task = self.get_object()
        if task.status != ReviewTask.STATUS_PENDING:
            return Response({"detail": "Task is not pending"}, status=status.HTTP_400_BAD_REQUEST)
        corrections = request.data.get("corrections", {})
        if not isinstance(corrections, dict):
            return Response({"detail": "corrections must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        unknown = sorted(set(corrections) - set(ExtractedData.EDITABLE_FIELDS))
        if unknown:
            return Response({"detail": f"These fields cannot be corrected: {', '.join(unknown)}"},
                            status=status.HTTP_400_BAD_REQUEST)

        extracted = getattr(task.document, "extracted_data", None)
        if not extracted:
            extracted = ExtractedData.objects.create(document=task.document, raw_extraction={})

        # Validate types (dates, decimals) through the serializer before saving.
        corrected = ExtractedDataSerializer(extracted, data=corrections, partial=True)
        if not corrected.is_valid():
            return Response(corrected.errors, status=status.HTTP_400_BAD_REQUEST)
        corrected.save()

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


class WebhookConfigViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WebhookConfigSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]

    def get_queryset(self):
        return self.scope(WebhookConfig.objects.all()).order_by("-created_at")

    def perform_create(self, serializer):
        # The organization always comes from the request, never from the body: a webhook
        # created for another organization would send that organization's data to this URL.
        serializer.save(organization_id=active_organization_id(self.request, require=True))


class WebhookDeliveryLogViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = WebhookDeliveryLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsOrgMember]
    org_field = "document__organization"

    def get_queryset(self):
        return self.scope(WebhookDeliveryLog.objects.all()).order_by("-created_at")
