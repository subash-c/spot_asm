import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "true").lower() in {"1", "true", "yes"}
SECRET_KEY = os.environ.get("SECRET_KEY") or (
    "dev-only-saferoute-key" if DEBUG else secrets.token_urlsafe(50)
)
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
FRONTEND_DIST = os.environ.get("FRONTEND_DIST", "")
# The hosted UI fetches public map data in the browser. Keep the legacy
# server-side provider endpoints available only for local development.
ENABLE_SERVER_PROVIDER_API = (
    os.environ.get("ENABLE_SERVER_PROVIDER_API", str(DEBUG)).lower()
    in {"1", "true", "yes"}
)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
# OpenStreetMap tile servers reject requests that arrive without a Referer.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
