from rest_framework.routers import DefaultRouter
from django.urls import path

from .views import UserViewSet, MeView

router = DefaultRouter()
router.register(r"", UserViewSet, basename="user")

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
] + router.urls
