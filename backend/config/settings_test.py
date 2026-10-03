"""Settings for the test suite: no Postgres, Redis, S3 or API keys needed."""
import os

from .settings import *  # noqa: F401,F403

SECRET_KEY = "test-secret-key"
# SQLite by default. Set TEST_DB=postgres (CI does) to catch Postgres-only errors such
# as numeric overflow; it uses the POSTGRES_* settings from settings.py.
if os.getenv("TEST_DB") != "postgres":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CELERY_TASK_ALWAYS_EAGER = True
# Eager retries re-run the task inline; let each run record its result (tests assert on DB state).
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_BROKER_URL = "memory://"
STORAGES = {
    # Not a filesystem storage: proves the pipeline never needs a local path.
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EXTRACTION_PROVIDER = "heuristic"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
