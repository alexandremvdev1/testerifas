import os
from pathlib import Path
import dj_database_url  # precisa estar no requirements.txt

BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------------------------------
# Básico / Segurança
# ----------------------------------------
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-d^@8398+$39b8o5k@&5mr0oh=(tlig3!pcwx*f3t_gdgqa$c#s",
)

# DEBUG: pego da env; se não tiver, fica False no servidor e True no local
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"

# Render manda o host dele, mas vamos montar assim:
_default_hosts = ["127.0.0.1", "localhost"]
env_hosts = os.getenv("ALLOWED_HOSTS")
if env_hosts:
    ALLOWED_HOSTS = _default_hosts + [h.strip() for h in env_hosts.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = _default_hosts

# CSRF (importante no Render)
CSRF_TRUSTED_ORIGINS = []
env_csrf = os.getenv("CSRF_TRUSTED_ORIGINS")
if env_csrf:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in env_csrf.split(",") if o.strip()]

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

    # Terceiros
    "rest_framework",

    # Seus apps
    "rifas",
]

# ----------------------------------------
# Middleware
# ----------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # se usar whitenoise no Render, ativa aqui:
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
# Banco de dados
# ----------------------------------------
# 1) padrão: sqlite (dev/local)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# 2) se tiver DATABASE_URL (Render / Neon), sobrescreve
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    DATABASES["default"] = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,      # mantém conexões
        ssl_require=True,      # Neon costuma precisar de SSL
    )

# ----------------------------------------
# Auth / Login
# ----------------------------------------
LOGIN_URL = "adminx_login"
LOGIN_REDIRECT_URL = "adminx_dashboard"

# ----------------------------------------
# REST Framework
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

# se quiser servir seus static do app rifas
STATICFILES_DIRS = []
rifas_static = BASE_DIR / "rifas" / "static"
if rifas_static.exists():
    STATICFILES_DIRS.append(rifas_static)

# whitenoise: servir estático no Render
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
# Default PK
# ----------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------
# Integrações / Config do projeto
# ----------------------------------------
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")

# SITE_URL: pega do ambiente; se não tiver, usa o local
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8000")

# ----------------------------------------
# Logs de SQL em dev
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
