import os
from pathlib import Path
import dj_database_url  # 👈 já está no requirements

# ----------------------------------------
# Paths
# ----------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------------------------------
# Básico / Segurança
# ----------------------------------------
# Em produção (Fly) vem do secret; em dev usa a que você já tinha
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-d^@8398+$39b8o5k@&5mr0oh=(tlig3!pcwx*f3t_gdgqa$c#s",
)

# DEBUG controlado por env
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"

# Hosts permitidos (Fly vai setar ALLOWED_HOSTS="rifas-online.fly.dev")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

# ----------------------------------------
# Apps
# ----------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "rifas",
]

# ----------------------------------------
# Middleware
# ----------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
# Banco de dados
# ----------------------------------------
# Prioridade:
# 1) se tiver DATABASE_URL -> usa (Neon, Render, etc.)
# 2) se não tiver -> cai no sqlite (dev)
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,  # Neon precisa de SSL
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ----------------------------------------
# Auth / Login
# ----------------------------------------
LOGIN_URL = "adminx_login"
LOGIN_REDIRECT_URL = "adminx_dashboard"

# ----------------------------------------
# REST Framework
# ----------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
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
STATIC_URL = "static/"

# quando for coletar no Fly
STATIC_ROOT = BASE_DIR / "staticfiles"

# estáticos do app
STATICFILES_DIRS = [
    BASE_DIR / "rifas" / "static",
]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ----------------------------------------
# Default PK
# ----------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------
# Integrações / Config do projeto
# ----------------------------------------
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")
