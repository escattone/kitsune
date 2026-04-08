from django.test.utils import override_settings
from elasticsearch import NotFoundError

from kitsune.products.tests import ProductFactory
from kitsune.questions.tests import AnswerFactory, QuestionFactory
from kitsune.search.documents import AnswerDocument, ProfileDocument, QuestionDocument
from kitsune.search.tests import ElasticTestCase
from kitsune.users.tests import GroupFactory, UserFactory


class ProfileDocumentSignalsTests(ElasticTestCase):
    def setUp(self):
        self.user = UserFactory()
        self.user_id = self.user.id

    def get_doc(self):
        return ProfileDocument.get(self.user_id)

    def test_user_save(self):
        self.user.username = "jdoe"
        self.user.save()

        self.assertEqual(self.get_doc().username, "jdoe")

    def test_profile_save(self):
        profile = self.user.profile
        profile.locale = "foobar"
        profile.save()

        self.assertEqual(self.get_doc().locale, "foobar")

    def test_user_groups_change(self):
        group = GroupFactory()
        self.user.groups.add(group)

        self.assertIn(group.id, self.get_doc().group_ids)

        self.user.groups.remove(group)

        self.assertNotIn(group.id, self.get_doc().group_ids)

    def test_user_products_change(self):
        profile = self.user.profile
        product = ProductFactory()
        profile.products.add(product)

        self.assertIn(product.id, self.get_doc().product_ids)

        profile.products.remove(product)

        self.assertNotIn(product.id, self.get_doc().product_ids)

    def test_user_delete(self):
        self.user.delete()

        with self.assertRaises(NotFoundError):
            self.get_doc()

    def test_profile_delete(self):
        self.user.profile.delete()

        with self.assertRaises(NotFoundError):
            self.get_doc()

    def test_group_delete(self):
        group = GroupFactory()
        self.user.groups.add(group)
        group.delete()

        self.assertEqual(self.get_doc().group_ids, [])

    def test_product_delete(self):
        profile = self.user.profile
        product = ProductFactory()
        profile.products.add(product)
        product.delete()

        self.assertEqual(self.get_doc().product_ids, [])


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class UserIsActiveReindexTests(ElasticTestCase):
    def setUp(self):
        self.user = UserFactory(is_active=True)
        self.question = QuestionFactory(creator=self.user)
        self.answer = AnswerFactory(question=self.question)

    def test_deactivating_user_updates_question_document(self):
        doc = QuestionDocument.get(self.question.id)
        self.assertTrue(doc.question_creator_is_active)

        self.user.is_active = False
        self.user.save()

        doc = QuestionDocument.get(self.question.id)
        self.assertFalse(doc.question_creator_is_active)

    def test_deactivating_user_updates_answer_document(self):
        doc = AnswerDocument.get(self.answer.id)
        self.assertTrue(doc.question_creator_is_active)

        self.user.is_active = False
        self.user.save()

        doc = AnswerDocument.get(self.answer.id)
        self.assertFalse(doc.question_creator_is_active)

    def test_reactivating_user_updates_question_document(self):
        self.user.is_active = False
        self.user.save()

        doc = QuestionDocument.get(self.question.id)
        self.assertFalse(doc.question_creator_is_active)

        self.user.is_active = True
        self.user.save()

        doc = QuestionDocument.get(self.question.id)
        self.assertTrue(doc.question_creator_is_active)

    def test_no_reindex_when_is_active_unchanged(self):
        self.user.username = "newname"
        self.user.save()

        doc = QuestionDocument.get(self.question.id)
        self.assertTrue(doc.question_creator_is_active)
