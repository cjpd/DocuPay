from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.users.auth import CsrfView, LoginView, LogoutView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Browser sign-in (httpOnly session cookie + CSRF token)
    path("api/auth/csrf/", CsrfView.as_view(), name="auth_csrf"),
    path("api/auth/login/", LoginView.as_view(), name="auth_login"),
    path("api/auth/logout/", LogoutView.as_view(), name="auth_logout"),
    # Scripts and server-to-server: JWT bearer tokens
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/organizations/", include("apps.organizations.urls")),
    path("api/users/", include("apps.users.urls")),
    path("api/documents/", include("apps.documents.urls")),
]
