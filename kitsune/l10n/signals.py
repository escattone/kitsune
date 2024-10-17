from django.db.models.signals import post_save
from django.dispatch import receiver

from kitsune.l10n.tasks import submit_for_localization
from kitsune.l10n.utils import manage_machine_translations, is_ready_for_localization
from kitsune.wiki.models import Document


@receiver(post_save, sender=Document, dispatch_uid="l10.manage_wiki_localization")
def manage_wiki_localization(sender, instance, created, **kwargs):
    # Practice good hygiene by cleaning any stale machine
    # translations within the context of this document.
    manage_machine_translations("clean", instance)

    # Submit the document for localization in all enabled locales as needed.
    if is_ready_for_localization(instance):
        submit_for_localization.delay(instance.id)
