from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Now


from kitsune.l10n.llm import get_localization
from kitsune.users.models import Profile
from kitsune.wiki.models import Document, Revision
from kitsune.wiki.config import REDIRECT_HTML, TYPO_SIGNIFICANCE


def get_timedelta_for(value_plus_unit_of_time):
    """
    Convenience function for returning a datetime.timedelta object from a string
    that includes an integer value plus a unit of time, separated by whitespace.
    For example, an input of "3 days" returns "timedelta(days=3)".
    """
    value, unit_of_time = value_plus_unit_of_time.split()
    return timedelta(**{unit_of_time: int(value)})


def is_ready_for_localization(doc):
    """
    Returns a boolean indicating whether the given document is localizable,
    and has a current revision that is ready for localization.
    """
    return (
        doc
        and (doc.locale == settings.WIKI_DEFAULT_LANGUAGE)
        and not doc.is_archived
        and doc.is_localizable
        and not doc.html.startswith(REDIRECT_HTML)
        and (current_rev := doc.current_revision)
        and current_rev.is_approved
        and current_rev.significance
        and (current_rev.significance > TYPO_SIGNIFICANCE)
        and current_rev.is_ready_for_localization
    )


def get_locales_lacking_translation(doc):
    """
    Returns a set of locales that lack a translation for the given document's
    current revision. The set of locales is limited to those that have been
    enabled for machine translations.
    """
    locales_already_localized = set(
        Document.objects.filter(
            parent=doc,
            locale__in=settings.SUMO_L10N_LOCALES_WITH_MACHINE_TRANSLATION,
        )
        .filter(
            # Does an up-to-date translation already exist that has either
            # already been approved or is awaiting review?
            Exists(
                Revision.objects.filter(
                    document=OuterRef("pk"),
                    based_on=doc.current_revision,
                ).filter(Q(is_approved=True) | Q(reviewed__isnull=True))
            )
        )
        .values_list("locale", flat=True)
    )
    return settings.SUMO_L10N_LOCALES_WITH_MACHINE_TRANSLATION - locales_already_localized


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


def create_machine_translations(doc):
    """
    Create machine translations of the given document's current revision for
    each locale enabled for machine translations but lacking a translation.
    """
    for locale in get_locales_lacking_translation(doc):
        create_machine_translation(doc, locale)


def create_machine_translation(doc, target_locale):
    """
    Get the machine translation of the given revision for the given target locale.
    """
    content = get_localization(doc, "content", target_locale)
    summary = get_localization(doc, "summary", target_locale)
    keywords = get_localization(doc, "keywords", target_locale)

    locale_doc = doc.translated_to(target_locale)

    if not locale_doc:
        title = get_localization(doc, "title", target_locale)

        # Create a new document for the locale if there isn't one already.
        locale_doc = Document.objects.create(
            parent=doc,
            locale=target_locale,
            title=title,
            slug=doc.slug,
            category=doc.category,
            allow_discussion=doc.allow_discussion,
        )

    # Create the localized revision.
    Revision.objects.create(
        content=content,
        summary=summary,
        keywords=keywords,
        document=locale_doc,
        creator=get_l10n_bot(),
        based_on=doc.current_revision,
    )


def manage_machine_translations(action, doc=None):
    """
    This function manages machine translations. It provides two actions,
    both of which operate within the context of the given document. The
    "clean" action marks all machine translations...

    Approve machine translations, within the context of the given document,
    that are still relevant, and have either been awaiting review for longer
    than the SUMO_L10N_REVIEW_GRACE_PERIOD, or have been rejected and no other
    translation has been approved within the SUMO_L10N_POST_REVIEW_GRACE_PERIOD.
    """
    assert action in ("clean", "publish")

    l10n_bot = get_l10n_bot()

    if not doc:
        # Consider the revisions of all localized documents within enabled locales.
        qs = Revision.objects.filter(
            document__parent__isnull=False,
            document__parent__is_archived=False,
            document__parent__is_localizable=True,
            document__parent__current_revision__isnull=False,
            document__locale__in=settings.SUMO_L10N_LOCALES_WITH_MACHINE_TRANSLATION,
        )
    elif doc.locale == settings.WIKI_DEFAULT_LANGUAGE:
        if doc.is_archived or not doc.is_localizable or not doc.current_revision:
            return
        # Consider the revisions of all of this document's localized documents
        # within enabled locales.
        qs = Revision.objects.filter(
            document__parent=doc,
            document__locale__in=settings.SUMO_L10N_LOCALES_WITH_MACHINE_TRANSLATION,
        )
    elif doc.locale in settings.SUMO_L10N_LOCALES_WITH_MACHINE_TRANSLATION:
        # Only consider the revisions of this localized document.
        qs = doc.revisions
    else:
        return

    qs = qs.filter(creator=l10n_bot, is_approved=False)

    if action == "clean":
        # Mark irrelevant machine translations that were awaiting review, as reviewed.
        qs.filter(
            reviewed__isnull=True,
        ).filter(
            # This machine translation is either out-of-date -- it's not based on
            # the current revision of its English parent -- or a different up-to-date
            # translation has already been approved for this localized document.
            ~Q(based_on=F("document__parent__current_revision"))
            | Q(document__current_revison__based_on=F("document__parent__current_revision"))
        ).update(
            reviewer=l10n_bot,
            reviewed=datetime.now(),
            comment="This machine translation has been superseded or is out-of-date.",
        )
        return

    review_grace_period = get_timedelta_for(settings.SUMO_L10N_REVIEW_GRACE_PERIOD)
    post_review_grace_period = get_timedelta_for(settings.SUMO_L10N_POST_REVIEW_GRACE_PERIOD)

    # We can't do an SQL update because we need to trigger the Revision model's "save" method.
    for rev in (
        qs.filter(
            based_on=F("document__parent__current_revision"),
        )
        .exclude(
            document__current_revison__based_on=F("document__parent__current_revision"),
        )
        .filter(
            (Q(reviewed__isnull=True) & Q(created__lt=Now() - review_grace_period))
            | (Q(reviewed__isnull=False) & Q(reviewed__lt=Now() - post_review_grace_period))
        )
        .all()
    ):
        if rev.reviewed is None:
            rev.is_approved = True
            rev.reviewed = datetime.now()
            rev.reviewer = l10n_bot
            rev.comment = (
                "This machine translation was automatically approved "
                "because it was not reviewed within the grace period of "
                f"{settings.SUMO_L10N_REVIEW_GRACE_PERIOD}."
            )
            rev.save()
        else:
            now = datetime.now()
            Revision.objects.create(
                created=now,
                reviewed=now,
                is_approved=True,
                creator=l10n_bot,
                reviewer=l10n_bot,
                document=rev.document_id,
                based_on=rev.based_on_id,
                summary=rev.summary,
                content=rev.content,
                keywords=rev.keywords,
                comment=(
                    f"This machine translation is a copy of {rev.created}, "
                    "which was reviewed and not approved. However, this copy "
                    "was created and automatically approved because an alternate "
                    "translation was not approved within the grace period of "
                    f"{settings.SUMO_L10N_POST_REVIEW_GRACE_PERIOD}."
                ),
            )
