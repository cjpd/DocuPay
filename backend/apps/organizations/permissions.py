from rest_framework import permissions

from .models import OrgMembership


class IsOrgMember(permissions.BasePermission):
    """
    Checks that the requesting user is a member of the object's organization.
    Expects the object to have an `organization` attribute.
    """

    def has_object_permission(self, request, view, obj) -> bool:
        if request.user.is_anonymous:
            return False
        org = getattr(obj, "organization", None)
        if org is None:
            return False
        return OrgMembership.objects.filter(user=request.user, organization=org).exists()
