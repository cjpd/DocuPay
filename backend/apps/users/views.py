from rest_framework import viewsets, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import CustomUser
from .serializers import UserSerializer


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """Read only. Accounts are created by sign-up or by an admin, not through this endpoint."""

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return CustomUser.objects.all().order_by("id")
        return CustomUser.objects.filter(id=user.id)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
