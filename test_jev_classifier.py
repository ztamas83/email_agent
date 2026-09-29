import tempfile
import os
import json
import unittest
import unittest.mock as mock

from email_classifier import EmailClassifier
from jev_classifier import JevEmailClassifier
from schemas import EmailAction

class TestJevEmailClassifier(unittest.TestCase):
    """Covers JevEmailClassifier with a mocked TypeSafe client (no network calls)."""

    RULES = [
        {
            "category": "travel",
            "category_criteria": "Tickets, reservations, itineraries",
            "action_criteria": "{user} is on the travelers list",
            "prompt": "Forward if {user} is travelling.",
            "apply_folder": "Travel",
            "should_forward": True,
        },
        {
            "category": "finance",
            "category_criteria": "Invoices and statements",
            "action_criteria": "Payment is due",
            "apply_folder": "Finance",
        },
    ]

    def setUp(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(self.RULES, f)
            self.rules_file = f.name
        self.addCleanup(os.remove, self.rules_file)
        env_patch = mock.patch.dict(os.environ, {}, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("CATEGORY_RULES_JSON", None)

        self.mock_llm = mock.MagicMock()
        self.classifier = JevEmailClassifier(
            llm=self.mock_llm,
            rules_file=self.rules_file,
            mailbox_user="Alice",
        )

    @staticmethod
    def _msg(subject="Your flight booking", sender="airline@example.com"):
        msg = mock.MagicMock()
        msg.subject = subject
        msg.from_ = sender
        msg.date_str = "Mon, 1 Jan 2024 10:00:00 +0000"
        msg.text = "Traveler: Alice"
        msg.html = None
        return msg

    def _choice_response(self, category):
        response = mock.MagicMock()
        response.answers = {"category": mock.MagicMock(choice=category)}
        return response

    def test_is_email_classifier(self):
        self.assertIsInstance(self.classifier, EmailClassifier)
        self.assertIs(self.classifier._llm, self.mock_llm)
        self.assertEqual(self.classifier.mailbox_user, "Alice")
        self.assertEqual(self.classifier.rules_file, self.rules_file)

    def test_missing_api_key_raises(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        with mock.patch("jev_classifier.load_dotenv"):
            with self.assertRaises(ValueError):
                JevEmailClassifier()

    def test_api_key_creates_typesafe_client(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        with mock.patch("jev_classifier.load_dotenv"), mock.patch(
            "jev_classifier.TypeSafeClient"
        ) as client_cls:
            classifier = JevEmailClassifier()
        client_cls.assert_called_once_with()
        self.assertIs(classifier._llm, client_cls.return_value)

    def test_load_rules_list_format_normalizes_criteria(self):
        rules = self.classifier.load_category_rules()
        self.assertEqual(set(rules), {"travel", "finance"})
        self.assertEqual(
            rules["travel"]["category_criteria"],
            "Tickets, reservations, itineraries",
        )
        self.assertEqual(rules["finance"]["action_criteria"], "Payment is due")

    def test_load_rules_dict_format(self):
        os.environ["CATEGORY_RULES_JSON"] = json.dumps(
            {"shopping": {"prompt": "Track orders."}, "security": "Check 2FA."}
        )
        rules = self.classifier.load_category_rules("/nonexistent.json")
        self.assertEqual(rules["shopping"]["prompt"], "Track orders.")
        self.assertEqual(rules["security"]["prompt"], "Check 2FA.")

    def test_format_and_enforce_rule_outputs(self):
        rule = {
            "apply_folder": "Travel",
            "should_forward": True,
            "mark_as_read": False,
            "forward_to": "x@y.z",
            "urgency": "high",
        }
        text = self.classifier.format_required_outputs_for_prompt(rule)
        self.assertIn("apply_folder: MUST be set to 'Travel'", text)
        self.assertIn("should_forward: MUST be set to true", text)
        self.assertEqual(self.classifier.format_required_outputs_for_prompt({}), "")

        action = EmailAction(
            category="travel",
            urgency="low",
            should_forward=False,
            reasoning="r",
        )
        enforced = self.classifier.enforce_rule_outputs(action, rule)
        self.assertEqual(enforced.apply_folder, "Travel")
        self.assertTrue(enforced.should_forward)
        self.assertFalse(enforced.mark_as_read)
        self.assertEqual(enforced.forward_to, "x@y.z")
        self.assertEqual(enforced.urgency, "high")

    def test_extract_email_text_html_fallback(self):
        msg = mock.MagicMock()
        msg.text = ""
        msg.html = "<style>x{}</style><p>Hi &amp; bye</p>"
        self.assertEqual(JevEmailClassifier.extract_email_text(msg), "Hi & bye")

    def test_no_rules_skips_llm(self):
        classifier = JevEmailClassifier(
            llm=self.mock_llm, rules_file="/nonexistent/rules.json"
        )
        action = classifier.classify_email(self._msg())
        self.assertEqual(action.category, "other")
        self.assertFalse(action.should_forward)
        self.mock_llm.system_one.assert_not_called()

    def test_step1_sends_only_headers_with_rule_criteria(self):
        self.mock_llm.system_one.return_value = self._choice_response("travel")
        with mock.patch.object(
            self.classifier, "structured_classifier", side_effect=RuntimeError("stop")
        ):
            with self.assertRaises(RuntimeError):
                self.classifier.classify_email(self._msg())

        args, kwargs = self.mock_llm.system_one.call_args
        self.assertEqual(
            args[0],
            {
                "incoming_email": {
                    "subject": "Your flight booking",
                    "sender": "airline@example.com",
                }
            },
        )
        self.assertNotIn("Traveler: Alice", json.dumps(args[0]))
        category_q = kwargs["questions"]["category"]
        self.assertEqual(
            dict(category_q.criteria),
            {
                "travel": "Tickets, reservations, itineraries",
                "finance": "Invoices and statements",
            },
        )

    def test_step2_passes_body_state_and_noul_question(self):
        self.mock_llm.system_one.return_value = self._choice_response("travel")
        with mock.patch.object(
            self.classifier, "structured_classifier", side_effect=RuntimeError("stop")
        ) as structured:
            with self.assertRaises(RuntimeError):
                self.classifier.classify_email(self._msg())

        kwargs = structured.call_args.kwargs
        state, question = kwargs["state"], kwargs["question"]
        self.assertEqual(state["body"], "Traveler: Alice")
        self.assertEqual(state["category"], "travel")
        self.assertEqual(state["mailbox_user"], "Alice")
        self.assertIn(
            "apply_folder: MUST be set to 'Travel'", state["required_outputs"]
        )
        self.assertIn(
            "Forward if Alice is travelling.", question["action"].instructions
        )

if __name__ == "__main__":
    unittest.main()
