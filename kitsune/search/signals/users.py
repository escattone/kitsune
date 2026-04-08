from django.contrib.auth.models import Group, User
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

from kitsune.products.models import Product
from kitsune.questions.models import Answer, Question
from kitsune.search.decorators import search_receiver
from kitsune.search.es_utils import (
    delete_object,
    index_object,
    index_objects_bulk,
    remove_from_field,
)
from kitsune.users.models import Profile


@search_receiver(post_save, User)
@search_receiver(post_save, Profile)
@search_receiver(m2m_changed, User.groups.through)
@search_receiver(m2m_changed, Profile.products.through)
def handle_profile_save(instance, **kwargs):
    index_object.delay("ProfileDocument", instance.pk)


@search_receiver(pre_save, User)
def track_user_is_active(instance, **kwargs):
    """Store the previous is_active value on the instance for post_save comparison."""
    if instance.pk:
        try:
            instance._was_active = (
                User.objects.filter(pk=instance.pk).values_list("is_active", flat=True).get()
            )
        except User.DoesNotExist:
            pass


@search_receiver(post_save, User)
def handle_user_is_active_change(instance, **kwargs):
    """Re-index a user's questions and answers when is_active changes."""
    was_active = getattr(instance, "_was_active", None)
    if (was_active is None) or (was_active == instance.is_active):
        return
    question_ids = list(Question.objects.filter(creator=instance).values_list("pk", flat=True))
    if question_ids:
        index_objects_bulk.delay("QuestionDocument", question_ids)
        answer_ids = list(
            Answer.objects.filter(question__creator=instance).values_list("pk", flat=True)
        )
        if answer_ids:
            index_objects_bulk.delay("AnswerDocument", answer_ids)


@search_receiver(post_delete, Profile)
def handle_profile_delete(instance, **kwargs):
    delete_object.delay("ProfileDocument", instance.pk)


@search_receiver(post_delete, Group)
def handle_group_delete(instance, **kwargs):
    remove_from_field.delay("ProfileDocument", "group_ids", instance.pk)


@search_receiver(post_delete, Product)
def handle_product_delete(instance, **kwargs):
    remove_from_field.delay("ProfileDocument", "product_ids", instance.pk)
