import json
import os
import tempfile
import unittest
from unittest import mock

from email_classifier import EmailClassifier
from jev_classifier import JevEmailClassifier
from schemas import EmailAction


class TestJevEmailClassifier(unittest.TestCase):
    """Covers JevEmailClassifier with a mocked TypeSafe client (no network calls)."""

    RULES = [
        {
            "category": "travel",
            "category_criteria": "Tickets, reservations, itineraries",
            "prompt": "Forward if {user} is travelling.",
            "apply_folder": "Travel",
            "should_forward": True,
            "mark_as_read": False,
        },
        {
            "category": "finance",
            "category_criteria": "Invoices and statements",
            "apply_folder": "Finance",
            "mark_as_read": False,
            "prompt": "",
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
        response.answers = {
            "category": mock.MagicMock(choice=category, confidence=0.86),
            "urgency": mock.MagicMock(score=0.1),
            "action": mock.MagicMock(noul=0.9),
        }

        response.model_dump_json.return_value = json.dumps(
            {"answers": {"category": {"choice": category}}}, sort_keys=True
        )
        return response

    @staticmethod
    def _step2_response(action, urgency, should_forward, mark_as_read):
        response = mock.MagicMock()
        response.answers = {
            "action": mock.MagicMock(noul=action),
            "urgency": mock.MagicMock(score=urgency),
            "should_forward": mock.MagicMock(noul=should_forward),
            "mark_as_read": mock.MagicMock(noul=mark_as_read),
        }

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
        with (
            mock.patch("jev_classifier.load_dotenv"),
            mock.patch("jev_classifier.TypeSafeClient") as client_cls,
        ):
            classifier = JevEmailClassifier()
        client_cls.assert_called_once_with()
        self.assertIs(classifier._llm, client_cls.return_value)

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

    def test_two_stage_responses_are_recorded_in_reasoning(self):
        step1_response = self._choice_response("travel")
        step2_response = self._step2_response(
            action=0.9, urgency=0.1, should_forward=0.86, mark_as_read=0.86
        )
        self.mock_llm.system_one.side_effect = [step1_response, step2_response]

        action = self.classifier.classify_email(self._msg())

        self.assertIsNotNone(action.reasoning)

    def test_step1_sends_only_headers_with_rule_criteria(self):
        self.mock_llm.system_one.side_effect = [
            self._choice_response("travel"),
            RuntimeError("stop"),
        ]
        with self.assertRaises(RuntimeError):
            self.classifier.classify_email(self._msg())

        args, kwargs = self.mock_llm.system_one.call_args_list[0]
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
        self.mock_llm.system_one.side_effect = [
            self._choice_response("travel"),
            RuntimeError("stop"),
        ]
        with self.assertRaises(RuntimeError):
            self.classifier.classify_email(self._msg())

        args, kwargs = self.mock_llm.system_one.call_args_list[1]
        state, questions = kwargs["state"], kwargs["questions"]
        self.assertEqual(state["incoming_email"]["body"], "Traveler: Alice")
        self.assertEqual(state["incoming_email"]["category"], "travel")
        self.assertEqual(state["incoming_email"]["mailbox_user"], "Alice")
        self.assertEqual(state["rule"], "Forward if Alice is travelling.")


if __name__ == "__main__":
    unittest.main()
