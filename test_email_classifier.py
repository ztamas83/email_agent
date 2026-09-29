import unittest
import unittest.mock as mock

from email_classifier import EmailClassifier
from schemas import EmailAction

class TestEmailClassifier(unittest.TestCase):
    """Verify that EmailClassifier is an ABC and cannot be directly instantiated."""

    def test_email_classifier_abc_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            EmailClassifier()

    def test_custom_email_classifier_subclass(self):
        """Verify that concrete implementations of EmailClassifier can be instantiated and used."""
        class MockClassifier(EmailClassifier):
            def classify_email(self, msg, rules_path=None):
                return EmailAction(
                    category="travel",
                    urgency="low",
                    should_forward=False,
                    apply_folder="Travel",
                    mark_as_read=True,
                    reasoning="Subclass test",
                )

            def header_classifier(self, **kwargs):
                pass

            def structured_classifier(self, **kwargs):
                pass

        clf = MockClassifier()
        self.assertIsInstance(clf, EmailClassifier)
        res = clf.classify_email(mock.MagicMock())
        self.assertEqual(res.category, "travel")

if __name__ == "__main__":
    unittest.main()
