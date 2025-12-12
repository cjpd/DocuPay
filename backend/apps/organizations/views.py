from rest_framework import viewsets, permissions

from .models import Organization
from .serializers import OrganizationSerializer
from .models import OrgMembership


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Organization.objects.filter(memberships__user=user).distinct()

    def perform_create(self, serializer):
        org = serializer.save()
        OrgMembership.objects.create(organization=org, user=self.request.user, role="owner")
