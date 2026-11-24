import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
ON_VERCEL = "VERCEL" in os.environ
DEBUG = os.environ.get("DEBUG") == "1" and not ON_VERCEL

SECRET_KEY = os.environ["SECRET_KEY"] if ON_VERCEL else "local-only"
ALLOWED_HOSTS = [".vercel.app"] if ON_VERCEL else ["localhost", "127.0.0.1"]

DATABASES = {
    "default": dj_database_url.parse(
        os.environ["DATABASE_URL"]
        if ON_VERCEL
        else os.environ.get(
            "DATABASE_URL", "postgres://tallypay:tallypay@127.0.0.1:5434/tallypay"
        )
    )
}

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "idempotency",
    "ledger",
    "merchants",
    "payments",
    "provider",
    "reconciliation",
    "refunds",
    "tasks",
    "wakeups",
    "webhooks",
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
        "DIRS": [BASE_DIR / "templates"],
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if ON_VERCEL else None
CSRF_COOKIE_SECURE = ON_VERCEL

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000")
QSTASH_URL = os.environ.get("QSTASH_URL", "https://qstash.upstash.io")
QSTASH_TOKEN = os.environ.get("QSTASH_TOKEN", "")
QSTASH_CURRENT_SIGNING_KEY = os.environ.get("QSTASH_CURRENT_SIGNING_KEY", "")
QSTASH_NEXT_SIGNING_KEY = os.environ.get("QSTASH_NEXT_SIGNING_KEY", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# Lets webhooks reach a server on this computer.
WEBHOOKS_ALLOW_LOCAL = not ON_VERCEL
