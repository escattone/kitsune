from django.db.models.signals import post_save
from django.dispatch import receiver

from kitsune.l10n.tasks import submit_for_localization
from kitsune.l10n.utils import is_ready_for_localization
from kitsune.wiki.models import Revision


@receiver(post_save, sender=Revision, dispatch_uid="l10.manage_wiki_localization")
def manage_wiki_localization(sender, instance, created, **kwargs):
    if is_ready_for_localization(instance):
        submit_for_localization.delay(instance.id)
