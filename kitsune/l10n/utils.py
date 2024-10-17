from django.conf import settings
from django.contrib.auth import get_user_model

from kitsune.users.models import Profile
from kitsune.wiki.models import Document


def is_ready_for_localization(rev):
    """
    Returns a boolean indicating whether the revision is ready for localization.
    This is a more stringent check than using the "is_ready_for_localization" field
    directly, and also checks whether the revision is the current revision of its
    document.
    """
    return (
        rev
        and rev.can_be_readied_for_localization()
        and rev.is_ready_for_localization
        and rev.document.is_localizable
        and (rev == rev.document.current_revision)
    )


def get_mt_enabled_locales_lacking_localization(rev):
    """
    Returns a set of locales that lack a localization for the given revision.
    The set of locales is limited to those that have been enabled for machine
    translations.
    """
    locales_already_localized = set(
        Document.objects.filter(
            parent=rev.document,
            revisions__based_on=rev,
            locale__in=settings.LOCALES_ENABLED_FOR_MACHINE_TRANSLATION,
        )
        .distinct()
        .values_list("locale", flat=True)
    )
    return set(settings.LOCALES_ENABLED_FOR_MACHINE_TRANSLATION) - locales_already_localized


def get_l10n_bot():
    """
    Returns the User instance that is the SUMO L10n Bot.
    """
    user, created = get_user_model().objects.get_or_create(
        username="sumo-l10n-bot", email="sumodev@mozilla.com"
    )
    if created:
        Profile.objects.create(user=user, name="SUMO Localization Bot")
    return user


def create_machine_translations(rev):
    """
    Create machine translations of the given revision for each locale enabled
    for machine translations but lacking a translation.
    """
    for locale in get_mt_enabled_locales_lacking_localization(rev):
        create_machine_translation(rev, locale)


def create_machine_translation(rev, locale):
    """
    Get the machine translation of the given revision for the given locale.
    """
    ...
