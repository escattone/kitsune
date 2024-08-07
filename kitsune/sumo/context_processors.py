from datetime import datetime

from django.conf import settings
from django.utils import translation

from kitsune.questions.models import AAQConfig


def global_settings(request):
    """Adds settings to the context."""
    return {"settings": settings}


def i18n(request):
    return {
        "LANG": (
            settings.LANGUAGE_URL_MAP.get(translation.get_language()) or translation.get_language()
        ),
        "DIR": "rtl" if translation.get_language_bidi() else "ltr",
    }


def aaq_languages(request):
    """Adds the list of AAQ languages to the context."""
    return {"AAQ_LANGUAGES": AAQConfig.objects.locales_list()}


def current_year(request):
    return {"CURRENT_YEAR": datetime.now().year}


def static_url_webpack(request):
    """Adds the static url without a trailing slash, for use by webpack."""
    return {"STATIC_URL_WEBPACK": settings.STATIC_URL.removesuffix("/")}
