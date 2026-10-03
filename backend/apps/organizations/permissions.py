from rest_framework import permissions

from .models import OrgMembership


class IsOrgMember(permissions.BasePermission):
    """
    Checks that the requesting user is a member of the object's organization.
    The organization is read from the object, or from its document or webhook config.
    """

    @staticmethod
    def organization_of(obj):
        for owner in (obj, getattr(obj, "document", None), getattr(obj, "webhook_config", None)):
            org_id = getattr(owner, "organization_id", None)
            if org_id is not None:
                return org_id
        return None

    def has_object_permission(self, request, view, obj) -> bool:
        if request.user.is_anonymous:
            return False
        org = self.organization_of(obj)
        if org is None:
            return False
        return OrgMembership.objects.filter(user=request.user, organization_id=org).exists()
