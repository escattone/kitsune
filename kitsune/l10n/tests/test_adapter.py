from kitsune.sumo.tests import TestCase
from kitsune.l10n.adapter import adapt_for_translation


class AdaptForTranslationTests(TestCase):
    def test_mix_of_links(self):
        # hooks = [
        #     "[[Image:An Image Title|width=300|height=400]]",
        #     "[[Video:A Video Title|width=400|height=600]]",
        #     "[[V:Another Video Title]]",
        #     "[[Include:Some Article Title]]",
        #     "[[I:Some Other Article Title]]",
        #     "[[Template:Some Template Title|v1=3|v2=5]]",
        #     "[[T:Some Other Template Title|v1=3|v2=5]]",
        #     "[[Button:refresh]]",
        #     "[[Button:preferences]]",
        #     "[[UI:details_start]]",
        #     "[[UI:details_end]]",
        #     "[[UI:device_migration_wizard]]",
        # ]
        # internal_links = [
        #     "[[An Article Title]]",
        #     "[[Another Article Title|as some text]]",
        # ]
        # external_links = [
        #     "[https://support.mozilla.org]",
        #     "[https://support.mozilla.org the Mozilla support website]",
        # ]
        text = ""
        result = adapt_for_translation(text)
        expected = ""
        self.assertEqual(result, expected)
