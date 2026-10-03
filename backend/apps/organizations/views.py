from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied

from .models import Organization, OrgMembership
from .scoping import has_role, member_org_ids
from .serializers import OrganizationSerializer


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Organization.objects.filter(id__in=member_org_ids(self.request.user)).order_by("name")

    def perform_create(self, serializer):
        org = serializer.save()
        OrgMembership.objects.create(organization=org, user=self.request.user, role="owner")

    def _require_admin(self, org):
        if not has_role(self.request.user, org.id):
            raise PermissionDenied("Only an owner or admin can change this organization.")

    def perform_update(self, serializer):
        self._require_admin(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        # Deleting an organization deletes all its documents.
        if not has_role(self.request.user, instance.id, roles=("owner",)):
            raise PermissionDenied("Only an owner can delete this organization.")
        instance.delete()
