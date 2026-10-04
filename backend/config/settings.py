import json
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me")
DEBUG = os.getenv("DEBUG", "0") == "1"
# Sessions and tokens are signed with SECRET_KEY: a known key lets anyone sign in as anyone.
if not DEBUG and (SECRET_KEY in ("", "change-me") or len(SECRET_KEY) < 32):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a random value of at least 32 characters.")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if os.getenv("CORS_ALLOWED_ORIGINS") else []
# The frontend sends the active organization and reads the page count of previews.
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "x-organization-id")
CORS_EXPOSE_HEADERS = ["X-Page-Count", "Content-Disposition"]
# Session cookie for the browser app. The frontend and the API must be on the same site
# (for example app.example.com and api.example.com) so the cookie is sent (SameSite=Lax).
CORS_ALLOW_CREDENTIALS = True
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0" if os.getenv("DEBUG", "0") == "1" else "1") == "1"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.getenv("SESSION_HOURS", "12")) * 3600
SESSION_SAVE_EVERY_REQUEST = True  # sliding expiry: active users stay signed in
CSRF_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if os.getenv("CSRF_TRUSTED_ORIGINS") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "corsheaders",
    "storages",
    "apps.common",
    "apps.organizations",
    "apps.users",
    "apps.documents",
    "apps.processing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "postgres"),
        "USER": os.getenv("POSTGRES_USER", "postgres"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "users.CustomUser"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # Scripts: JWT bearer tokens. Browser: session cookie, CSRF enforced on writes.
        # JWT comes first so a signed-out request gets 401 (with WWW-Authenticate), not 403.
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_THROTTLE_RATES": {"login": os.getenv("LOGIN_RATE_LIMIT", "10/min")},
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
# Results live in Postgres (Document/ExtractedData), so Celery needs no result backend.
CELERY_TASK_IGNORE_RESULT = True
# Extraction waits on LLM APIs (I/O bound). It gets its own queue so it never
# blocks other work; run a worker with: celery -A config worker -Q extraction,celery
CELERY_TASK_ROUTES = {"apps.processing.tasks.process_document": {"queue": "extraction"}}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Run with: celery -A config beat
CELERY_BEAT_SCHEDULE = {
    "fail-stale-documents": {"task": "apps.processing.tasks.fail_stale_documents", "schedule": 300.0},
}

# Extraction pipeline
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", "heuristic")  # anthropic | openai | heuristic
EXTRACTION_ESCALATE = os.getenv("EXTRACTION_ESCALATE", "1") == "1"
PROCESSING_MAX_PAGES = int(os.getenv("PROCESSING_MAX_PAGES", "20"))
PROCESSING_MAX_FILE_BYTES = int(os.getenv("PROCESSING_MAX_FILE_BYTES", str(20 * 1024 * 1024)))
# Seconds per LLM request; with 1 SDK retry, fast + strong attempts fit inside the task's 300 s soft limit.
EXTRACTION_REQUEST_TIMEOUT = int(os.getenv("EXTRACTION_REQUEST_TIMEOUT", "60"))
ANTHROPIC_FAST_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")
ANTHROPIC_STRONG_MODEL = os.getenv("ANTHROPIC_STRONG_MODEL", "claude-opus-5-5")
ANTHROPIC_STRONG_EFFORT = os.getenv("ANTHROPIC_STRONG_EFFORT", "medium")
ANTHROPIC_REFUSAL_FALLBACKS = os.getenv("ANTHROPIC_REFUSAL_FALLBACKS", "1") == "1"
OPENAI_FAST_MODEL = os.getenv("OPENAI_FAST_MODEL", "gpt-5-mini")
OPENAI_STRONG_MODEL = os.getenv("OPENAI_STRONG_MODEL", "gpt-5")
# Optional cost tracking for OpenAI: {"model": [input $/MTok, output $/MTok]} as JSON.
OPENAI_PRICING = json.loads(os.getenv("OPENAI_PRICING", "{}"))

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")

use_s3 = bool(
    AWS_STORAGE_BUCKET_NAME
    and AWS_STORAGE_BUCKET_NAME.lower() != "changeme"
    and AWS_S3_ENDPOINT_URL
    and AWS_S3_ENDPOINT_URL.lower() != "changeme"
)

if use_s3:
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
else:
    # Local storage fallback for development
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
