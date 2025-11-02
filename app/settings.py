import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------------------------------
# Básico / Segurança
# ----------------------------------------
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-d^@8398+$39b8o5k@&5mr0oh=(tlig3!pcwx*f3t_gdgqa$c#s",
)

# se não mandar DJANGO_DEBUG, fica False no Render
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"

# ----------------------------------------
# Hosts
# ----------------------------------------
env_hosts = os.getenv("ALLOWED_HOSTS")
if env_hosts:
    # se vier da env, usamos o que veio
    ALLOWED_HOSTS = [h.strip() for h in env_hosts.split(",") if h.strip()]
else:
    # para facilitar teu deploy agora:
    ALLOWED_HOSTS = ["*"]

# CSRF trusted (usa a env se tiver)
env_csrf = os.getenv("CSRF_TRUSTED_ORIGINS")
if env_csrf:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in env_csrf.split(",") if o.strip()]
else:
    # em produção coloca aqui: https://seu-app.onrender.com
    CSRF_TRUSTED_ORIGINS = []

# ----------------------------------------
# Apps
# ----------------------------------------
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # terceiros
    "rest_framework",

    # seus apps
    "rifas",
]

# ----------------------------------------
# Middleware
# ----------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # whitenoise para servir estático no Render
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "app.urls"

# ----------------------------------------
# Templates
# ----------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "app.wsgi.application"

# ----------------------------------------
# Banco
# ----------------------------------------
# padrão: sqlite (roda local sem nada)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# se vier DATABASE_URL (Render / Postgres), substitui
db_url = os.getenv("DATABASE_URL")
if db_url:
    DATABASES["default"] = dj_database_url.parse(
        db_url,
        conn_max_age=600,
        ssl_require=False,  # <- pro Postgres da Render / teu caso
    )

# ----------------------------------------
# Auth / Login
# ----------------------------------------
LOGIN_URL = "adminx_login"
LOGIN_REDIRECT_URL = "adminx_dashboard"

# ----------------------------------------
# DRF
# ----------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
}

# ----------------------------------------
# Internacionalização
# ----------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Araguaina"
USE_I18N = True
USE_TZ = True

# ----------------------------------------
# Static / Media
# ----------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = []
rifas_static = BASE_DIR / "rifas" / "static"
if rifas_static.exists():
    STATICFILES_DIRS.append(rifas_static)

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ----------------------------------------
# Django
# ----------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------
# Integrações
# ----------------------------------------
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")

# ----------------------------------------
# Logging (só em dev)
# ----------------------------------------
if DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "console": {"class": "logging.StreamHandler"},
        },
        "loggers": {
            "django.db.backends": {
                "handlers": ["console"],
                "level": "INFO",
            },
        },
    }
