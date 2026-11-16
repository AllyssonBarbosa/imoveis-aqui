"""
Django settings for config project.
"""

import re
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-trocar-em-producao")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'rest_framework.authtoken',

    'core',
    'localizacao',
    'empresas',
    'contas',
    'proprietarios',
    'imoveis',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': env.db(
        'DATABASE_URL',
        default='postgres://postgres:postgres@localhost:5432/imoveis_aqui',
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

AUTH_USER_MODEL = 'contas.Usuario'


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static & media files

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Django REST Framework

# Taxa das rotas públicas da vitrine. O DRF só interpreta o texto no primeiro uso (formato inválido viraria
# 500 em todas as rotas públicas), então valida na partida: `<número>/<período>`, ex. 120/min, 5/s, 1000/hour.
VITRINE_TAXA_ANONIMA = env('VITRINE_TAXA_ANONIMA', default='120/min')
if not re.fullmatch(r'[0-9]+/[smhd][a-z]*', VITRINE_TAXA_ANONIMA):
    raise ImproperlyConfigured(
        f"VITRINE_TAXA_ANONIMA inválida: {VITRINE_TAXA_ANONIMA!r}. Use <número>/<período>, "
        "por exemplo 120/min."
    )

# Quantos proxies confiáveis existem na frente da API (0 = nenhum, usa o IP da conexão).
NUM_PROXIES = env.int('NUM_PROXIES', default=0)
# Se a variável foi informada de propósito. Fora do DEBUG, o padrão 0 atrás de proxy reverso junta todos
# os visitantes no IP do proxy (um só balde de limite): a verificação `core.W001` (`check --deploy`) avisa.
NUM_PROXIES_CONFIGURADO = 'NUM_PROXIES' in env.ENVIRON

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # Taxa por IP das rotas públicas da vitrine (ThrottleVitrinePublica), folgada
    # para a navegação de uma pessoa no app; ajustável sem mudar código.
    # Não há DEFAULT_THROTTLE_CLASSES: a API autenticada não é limitada.
    'DEFAULT_THROTTLE_RATES': {
        'vitrine_publica': VITRINE_TAXA_ANONIMA,
    },
    # 0 = usa o IP da conexão e ignora o X-Forwarded-For, que o cliente forja.
    # Atrás de proxy reverso, informe quantos proxies confiáveis existem.
    'NUM_PROXIES': NUM_PROXIES,
}


# Painel web

LOGIN_URL = 'contas:login'
LOGIN_REDIRECT_URL = 'contas:painel'
LOGOUT_REDIRECT_URL = 'contas:login'
