from django.conf import settings
from django.contrib.auth.models import User

from kitsune.sumo.tests import TestCase
from kitsune.users.tests import GroupFactory, UserFactory
from kitsune.wiki.handlers import DocumentListener
from kitsune.wiki.tests import ApprovedRevisionFactory, DocumentFactory, HelpfulVoteFactory


class TestDocumentListener(TestCase):
    def setUp(self):
        self.user = UserFactory()
        User.objects.get_or_create(username=settings.SUMO_BOT_USERNAME)
        self.listener = DocumentListener()

        self.content_group = GroupFactory(name=settings.SUMO_CONTENT_GROUP)
        self.group_user1 = UserFactory()
        self.group_user2 = UserFactory()
        self.content_group.user_set.add(self.group_user1, self.group_user2)

    def test_document_contributor_replacement(self):
        """Test that sole contributor is replaced with content group members."""
        document = DocumentFactory()
        document.contributors.add(self.user)

        self.listener.on_user_deletion(self.user)

        self.assertFalse(document.contributors.filter(id=self.user.id).exists())

        self.assertTrue(document.contributors.filter(id=self.group_user1.id).exists())
        self.assertTrue(document.contributors.filter(id=self.group_user2.id).exists())

    def test_document_multiple_contributors(self):
        """Test that user is removed but other contributors remain."""
        document = DocumentFactory()
        other_contributor = UserFactory()
        document.contributors.add(self.user, other_contributor)

        self.listener.on_user_deletion(self.user)

        self.assertFalse(document.contributors.filter(id=self.user.id).exists())

        self.assertTrue(document.contributors.filter(id=other_contributor.id).exists())

        self.assertFalse(document.contributors.filter(id=self.group_user1.id).exists())
        self.assertFalse(document.contributors.filter(id=self.group_user2.id).exists())

    def test_multiple_documents(self):
        """Test handling multiple documents with different contributor scenarios."""
        doc1 = DocumentFactory()
        doc1.contributors.add(self.user)

        doc2 = DocumentFactory()
        other_contributor = UserFactory()
        doc2.contributors.add(self.user, other_contributor)

        doc3 = DocumentFactory()
        doc3.contributors.add(other_contributor)

        self.listener.on_user_deletion(self.user)

        self.assertFalse(doc1.contributors.filter(id=self.user.id).exists())
        self.assertTrue(doc1.contributors.filter(id=self.group_user1.id).exists())
        self.assertTrue(doc1.contributors.filter(id=self.group_user2.id).exists())

        self.assertFalse(doc2.contributors.filter(id=self.user.id).exists())
        self.assertTrue(doc2.contributors.filter(id=other_contributor.id).exists())
        self.assertFalse(doc2.contributors.filter(id=self.group_user1.id).exists())

        self.assertTrue(doc3.contributors.filter(id=other_contributor.id).exists())
        self.assertEqual(doc3.contributors.count(), 1)

    def test_revision_user_fields_replacement(self):
        reviewer = UserFactory()
        contributor = UserFactory()
        rev1 = ApprovedRevisionFactory(
            creator=contributor,
            reviewer=reviewer,
            readied_for_localization_by=reviewer,
        )
        rev2 = ApprovedRevisionFactory(
            creator=self.user,
            reviewer=reviewer,
            readied_for_localization_by=reviewer,
        )
        rev3 = ApprovedRevisionFactory(
            creator=contributor,
            reviewer=self.user,
            readied_for_localization_by=self.user,
        )

        self.listener.on_user_deletion(self.user)

        for rev in (rev1, rev2, rev3):
            rev.refresh_from_db()

        self.assertEqual(rev1.creator.username, contributor.username)
        self.assertEqual(rev1.reviewer.username, reviewer.username)
        self.assertEqual(rev1.readied_for_localization_by.username, reviewer.username)
        self.assertEqual(rev2.creator.username, settings.SUMO_BOT_USERNAME)
        self.assertEqual(rev2.reviewer.username, reviewer.username)
        self.assertEqual(rev2.readied_for_localization_by.username, reviewer.username)
        self.assertEqual(rev3.creator.username, contributor.username)
        self.assertEqual(rev3.reviewer.username, settings.SUMO_BOT_USERNAME)
        self.assertEqual(rev3.readied_for_localization_by.username, settings.SUMO_BOT_USERNAME)

    def test_helpfulvote_creator_replacement(self):
        voter = UserFactory()
        v1 = HelpfulVoteFactory(creator=voter)
        v2 = HelpfulVoteFactory(creator=self.user)

        self.listener.on_user_deletion(self.user)

        v1.refresh_from_db()
        v2.refresh_from_db()

        self.assertEqual(v1.creator.username, voter.username)
        self.assertEqual(v2.creator.username, settings.SUMO_BOT_USERNAME)
