from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from kitsune.community.models import DeletedContribution
from kitsune.questions.models import Answer, Question
from kitsune.wiki.models import Revision


@receiver(
    pre_delete,
    sender=Question,
    dispatch_uid="community.record_deleted_answer_contributions",
)
def record_deleted_answer_contributions(sender, instance, **kwargs):
    """
    When a question is about to be deleted, record all non-spam contributions
    by users other than the creator of the question.
    """
    question = instance
    answer_content_type = ContentType.objects.get_for_model(Answer)
    for answer in question.answers.exclude(creator=question.creator).filter(is_spam=False):
        dc = DeletedContribution.objects.create(
            content_type=answer_content_type,
            contributor=answer.creator,
            contribution_timestamp=answer.created,
            locale=question.locale,
        )
        dc.products.set([question.product])


@receiver(
    pre_delete,
    sender=Revision,
    dispatch_uid="community.record_deleted_revision_contribution",
)
def record_deleted_revision_contribution(sender, instance, **kwargs):
    """
    When an approved revision is about to be deleted, record the contribution of its creator.
    """
    if instance.is_approved:
        # This is extremely rare. In fact, I think the only time this can happen is when
        # an approved revision for a translated document is cascade deleted because it's
        # based-on an unapproved revision created by a user that has been deleted.
        dc = DeletedContribution.objects.create(
            content_type=ContentType.objects.get_for_model(Revision),
            contributor=instance.creator,
            contribution_timestamp=instance.created,
            locale=instance.document.locale,
        )
        dc.products.set(instance.document.original.products.all())
