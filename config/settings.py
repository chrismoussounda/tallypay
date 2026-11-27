import os
from pathlib import Path

import dj_database_url
from django.utils.csp import CSP

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
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "accounts",
    "dashboard",
    "demos",
    "idempotency",
    "ledger",
    "limits",
    "merchants",
    # For the assets of the API documentation.
    "ninja",
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
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.looks.FrenchUntilChosen",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.SessionLifetime",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.looks.theme",
            ]
        },
    }
]
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
# Kept in UTC, shown in Congo's time.
TIME_ZONE = "Africa/Brazzaville"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if ON_VERCEL else None
CSRF_COOKIE_SECURE = ON_VERCEL

LANGUAGE_CODE = "fr"
LANGUAGES = [("en", "English"), ("fr", "Français")]
LOCALE_PATHS = [BASE_DIR / "locale"]

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "sign-in"
# 15 characters, as there is no second factor.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": f"django.contrib.auth.password_validation.{name}",
        "OPTIONS": options,
    }
    for name, options in [
        ("UserAttributeSimilarityValidator", {}),
        ("MinimumLengthValidator", {"min_length": 15}),
        ("CommonPasswordValidator", {}),
        ("NumericPasswordValidator", {}),
    ]
]
# An hour without any request.
SESSION_COOKIE_AGE = 3600
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SECURE = ON_VERCEL
SESSION_COOKIE_NAME = "__Host-session" if ON_VERCEL else "session"
CSRF_COOKIE_NAME = "__Host-csrf" if ON_VERCEL else "csrftoken"

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "base-uri": [CSP.NONE],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "object-src": [CSP.NONE],
}

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000")
QSTASH_URL = os.environ.get("QSTASH_URL", "https://qstash.upstash.io")
QSTASH_TOKEN = os.environ.get("QSTASH_TOKEN", "")
QSTASH_CURRENT_SIGNING_KEY = os.environ.get("QSTASH_CURRENT_SIGNING_KEY", "")
QSTASH_NEXT_SIGNING_KEY = os.environ.get("QSTASH_NEXT_SIGNING_KEY", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# Lets webhooks reach a server on this computer.
WEBHOOKS_ALLOW_LOCAL = not ON_VERCEL
