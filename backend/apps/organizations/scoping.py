"""
Tenant scoping for API views.

Every queryset that holds tenant data goes through OrgScopedMixin, so a view
cannot forget the organization filter. The active organization comes from the
X-Organization-ID header (or ?organization=) and must be one of the user's
memberships. A user with one membership needs no header.
"""
from typing import List, Optional

from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import OrgMembership

ORG_HEADER = "HTTP_X_ORGANIZATION_ID"
ADMIN_ROLES = ("owner", "admin")


def member_org_ids(user) -> List[int]:
    if not user or not user.is_authenticated:
        return []
    return list(OrgMembership.objects.filter(user=user).values_list("organization_id", flat=True))


def active_organization_id(request, require: bool = False) -> Optional[int]:
    """The organization this request acts in.

    With require=True (writes), a user in several organizations must say which one.
    For reads without a selection, the caller scopes to all of the user's organizations.
    """
    cached = getattr(request, "_active_org", ...)
    ids = member_org_ids(request.user)
    if cached is ...:
        raw = request.META.get(ORG_HEADER) or request.query_params.get("organization")
        if raw:
            try:
                org_id = int(raw)
            except (TypeError, ValueError):
                raise ValidationError({"organization": "Organization id must be a number."})
            if org_id not in ids:
                raise PermissionDenied("You are not a member of this organization.")
            cached = org_id
        else:
            cached = ids[0] if len(ids) == 1 else None
        request._active_org = cached
    if cached is None and require:
        if not ids:
            raise PermissionDenied("You are not a member of any organization.")
        raise ValidationError({"organization": "You belong to several organizations. Send the X-Organization-ID header."})
    return cached


def has_role(user, organization_id: int, roles=ADMIN_ROLES) -> bool:
    return OrgMembership.objects.filter(user=user, organization_id=organization_id, role__in=roles).exists()


class OrgScopedMixin:
    """Filter get_queryset() to the active organization (or all of the user's organizations).

    Set `org_field` to the path of the organization foreign key, for example
    "organization" or "document__organization".
    """

    org_field = "organization"

    def scope(self, queryset):
        org_id = active_organization_id(self.request)
        ids = [org_id] if org_id is not None else member_org_ids(self.request.user)
        return queryset.filter(**{f"{self.org_field}_id__in": ids})
