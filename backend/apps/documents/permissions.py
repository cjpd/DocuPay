from rest_framework import permissions


class DocumentPermission(permissions.IsAuthenticated):
    """
    Placeholder permission: later enforce org-based access control.
    """

    pass
