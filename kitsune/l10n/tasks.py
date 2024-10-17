from celery import shared_task

from kitsune.l10n.utils import create_machine_translations, is_ready_for_localization
from kitsune.wiki.models import Revision


@shared_task
def submit_for_localization(revision_id):
    rev = Revision.objects.select_related("document").get(id=revision_id)

    if not is_ready_for_localization(rev):
        return

    create_machine_translations(rev)
