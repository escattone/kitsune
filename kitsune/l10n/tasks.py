from datetime import timedelta

from celery import shared_task
from django.conf import settings

from django.db.models.functions import Now

from kitsune.l10n.utils import create_machine_translations, get_l10n_bot, is_ready_for_localization
from kitsune.wiki.models import Document, Revision


@shared_task
def submit_for_localization(document_id):
    doc = Document.objects.select_related("current_revision").get(id=document_id)

    if not is_ready_for_localization(doc):
        return

    create_machine_translations(doc)


@shared_task
def heartbeat():
    """ """
    # Check for automatic approvals when:
    #    (1) A l10n-bot created revision has not been reviewed and more than
    #        settings.SUMO_L10N_REVIEW_GRACE_PERIOD has elapsed since it was created.
    #    (2) A l10n-bot created revision has been reviewed but not approved, and a
    #        newer revision for the doc hasn't been approved, and more than
    #        settings.SUMO_L10N_POST_REVIEW_GRACE_PERIOD has elapsed since it was
    #        reviewed.
    l10n_bot = get_l10n_bot()

    value, unit_of_time = settings.SUMO_L10N_REVIEW_GRACE_PERIOD.split()

    review_grace_period = timedelta(**{unit_of_time: int(value)})

    for rev in (
        Revision.objects.filter(
            creator=l10n_bot,
            is_approved=False,
            reviewed__isnull=True,
            document__is_archived=False,
            document__is_template=False,
            document__parent__isnull=False,
            created__lt=Now() - review_grace_period,
        )
        .select_related("document")
        .all()
    ):
        # Are there cases when this bot-created rev is out-of-date? If so, it should
        # be considered reviewed and not approved by the l10n-bot user. For example,
        # if its document already has current rev whose based_on rev is equal to the
        # parent doc's current rev.
        #
        # (1) based_on rev is not the parent doc's current rev
        # (2)
        ...
