"""
Browser sign-in with a Django session cookie.

The session cookie is httpOnly, so page scripts can never read it (an XSS bug cannot
steal the session). Requests that change data must carry the CSRF token, which the
frontend gets from /api/auth/csrf/ and keeps in memory. Scripts and other servers can
still use JWT bearer tokens (/api/auth/token/), which need no CSRF token.
"""
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token, rotate_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView

from .throttles import LoginUsernameThrottle
from rest_framework.views import APIView

from .serializers import UserSerializer


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Sets the CSRF cookie and returns the token for the X-CSRFToken header."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


# CSRF is checked on sign-in too: otherwise another site could sign a visitor in to the
# attacker's account (login CSRF).
@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle, LoginUsernameThrottle]
    throttle_scope = "login"

    def post(self, request):
        user = authenticate(request, username=request.data.get("username", ""), password=request.data.get("password", ""))
        if user is None or not user.is_active:
            return Response({"detail": "Wrong username or password."}, status=status.HTTP_400_BAD_REQUEST)
        login(request, user)  # new session id: no session fixation
        rotate_token(request)
        return Response({"user": UserSerializer(user).data, "csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ThrottledTokenView(TokenObtainPairView):
    """JWT sign-in for scripts, with the same limits as the browser sign-in."""

    throttle_classes = [ScopedRateThrottle, LoginUsernameThrottle]
    throttle_scope = "login"
