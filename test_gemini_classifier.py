import tempfile
import os
import json
import unittest
import unittest.mock as mock

from email_classifier import EmailClassifier
from gemini_classifier import (
    GeminiEmailClassifier,
    extract_email_text,
    load_category_rules,
    classify_email,
)
from schemas import EmailAction, HeaderClassification

class TestGeminiEmailClassifier(unittest.TestCase):
    def test_extract_email_text(self):
        # Test both class static method and instance method
        classifier_inst = GeminiEmailClassifier(llm=mock.MagicMock())

        # 1. Pure plain-text email (non-HTML)
        msg_plain = mock.MagicMock()
        msg_plain.text = "Hello! Your flight is confirmed."
        msg_plain.html = None
        self.assertEqual(
            GeminiEmailClassifier.extract_email_text(msg_plain),
            "Hello! Your flight is confirmed.",
        )
        self.assertEqual(
            classifier_inst.extract_email_text(msg_plain),
            "Hello! Your flight is confirmed.",
        )
        self.assertEqual(
            extract_email_text(msg_plain),
            "Hello! Your flight is confirmed.",
        )

        # 2. HTML-only email (no plain text part)
        msg_html = mock.MagicMock()
        msg_html.text = None
        msg_html.html = "<html><body><h1>Flight Ticket</h1><p>Your booking &amp; ticket are confirmed.</p></body></html>"
        text = GeminiEmailClassifier.extract_email_text(msg_html)
        self.assertIn("Flight Ticket", text)
        self.assertIn("booking & ticket are confirmed.", text)
        self.assertNotIn("<html>", text)
        self.assertNotIn("<p>", text)

        # 3. Multipart email (prefers plain text)
        msg_multi = mock.MagicMock()
        msg_multi.text = "Plain text version"
        msg_multi.html = "<p>HTML version</p>"
        self.assertEqual(
            GeminiEmailClassifier.extract_email_text(msg_multi),
            "Plain text version",
        )

        # 4. Empty email
        msg_empty = mock.MagicMock()
        msg_empty.text = ""
        msg_empty.html = ""
        self.assertEqual(GeminiEmailClassifier.extract_email_text(msg_empty), "")

    def test_two_stage_triage_privacy(self):
        msg_personal = mock.MagicMock()
        msg_personal.from_ = "friend@example.com"
        msg_personal.subject = "Coffee catchup"
        msg_personal.date_str = "Mon, 28 Sep 2026"
        msg_personal.text = "SUPER PRIVATE PERSONAL SECRET BODY"
        msg_personal.html = None

        msg_travel = mock.MagicMock()
        msg_travel.from_ = "reservations@airline.com"
        msg_travel.subject = "Flight Confirmation"
        msg_travel.date_str = "Mon, 28 Sep 2026"
        msg_travel.text = "Flight SH-102 confirmed for Test A Testsson."
        msg_travel.html = None

        header_personal = HeaderClassification(
            category="personal",
            urgency="low",
            reasoning="Personal message from a friend.",
        )

        header_travel = HeaderClassification(
            category="travel",
            urgency="high",
            reasoning="Flight reservation notification.",
        )

        travel_action = EmailAction(
            category="travel",
            urgency="high",
            should_forward=True,
            forward_to="forwarded@example.com",
            apply_folder="Travel",
            mark_as_read=True,
            reasoning="Confirmed passenger matches.",
        )

        mock_llm = mock.MagicMock()
        mock_step1 = mock.MagicMock()
        mock_step2 = mock.MagicMock()
        mock_llm.with_structured_output.side_effect = lambda schema: {
            HeaderClassification: mock_step1,
            EmailAction: mock_step2,
        }[schema]

        classifier = GeminiEmailClassifier(llm=mock_llm, mailbox_user="Tamas")

        active_rules = {
            "travel": {
                "category": "travel",
                "prompt": "Check flight confirmation for {user}.",
            }
        }

        # 1. Personal email: Step 1 classifies as 'personal', has no prompt in rules, body is WITHHELD, Step 2 is NEVER called
        with mock.patch.object(classifier, "load_category_rules", return_value=active_rules):
            mock_step1.invoke.return_value = header_personal
            action = classifier.classify_email(msg_personal)
            mock_llm.with_structured_output.assert_called_once_with(
                HeaderClassification
            )
            mock_step1.invoke.assert_called_once()
            # Step 2 must NEVER be called for personal email
            mock_step2.invoke.assert_not_called()
            self.assertEqual(action.category, "personal")
            self.assertFalse(action.should_forward)
            self.assertIn("Privacy: body withheld from LLM", action.reasoning)

        # 2. Travel email: Step 1 classifies as 'travel', which has a prompt in rules. Step 2 IS called with body!
        mock_llm.reset_mock()
        mock_step1.reset_mock()
        mock_step2.reset_mock()
        with mock.patch.object(classifier, "load_category_rules", return_value=active_rules):
            mock_step1.invoke.return_value = header_travel
            mock_step2.invoke.return_value = travel_action
            action = classifier.classify_email(msg_travel)
            self.assertEqual(
                mock_llm.with_structured_output.call_args_list,
                [mock.call(HeaderClassification), mock.call(EmailAction)],
            )
            mock_step1.invoke.assert_called_once()
            mock_step2.invoke.assert_called_once()
            # Verify body was passed to Step 2
            step2_prompt = mock_step2.invoke.call_args[0][0]
            self.assertIn("Flight SH-102 confirmed for Test A Testsson.", step2_prompt)
            # Verify {user} was formatted correctly with mailbox_user
            self.assertIn("Check flight confirmation for Tamas.", step2_prompt)
            self.assertEqual(action.category, "travel")
            self.assertTrue(action.should_forward)

    def test_custom_category_rules_step2(self):
        custom_rules = [
            {
                "category": "finance",
                "prompt": "Custom finance prompt: Check if amount > $500.",
            },
            {
                "category": "travel",
                "prompt": "Custom travel prompt: Check if flight is on SkyHigh.",
            },
        ]

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(custom_rules, f)
            rules_file = f.name

        try:
            mock_step1 = mock.MagicMock()
            mock_step2 = mock.MagicMock()
            mock_llm = mock.MagicMock()
            mock_llm.with_structured_output.side_effect = lambda schema: {
                HeaderClassification: mock_step1,
                EmailAction: mock_step2,
            }[schema]
            classifier = GeminiEmailClassifier(
                rules_file=rules_file,
                llm=mock_llm,
            )

            # 1. Verify load_category_rules
            loaded = classifier.load_category_rules()
            self.assertIn("finance", loaded)
            self.assertIn("travel", loaded)
            self.assertEqual(
                loaded["finance"]["prompt"],
                "Custom finance prompt: Check if amount > $500.",
            )

            # 2. When finance email arrives: Step 2 sends ONLY finance prompt!
            msg_finance = mock.MagicMock()
            msg_finance.from_ = "billing@corp.com"
            msg_finance.subject = "Invoice #402"
            msg_finance.date_str = "Mon, 28 Sep 2026"
            msg_finance.text = "Amount: $750 due in 5 days."
            msg_finance.html = None

            header_finance = HeaderClassification(
                category="finance",
                urgency="medium",
                reasoning="Invoice detected.",
            )
            finance_action = EmailAction(
                category="finance",
                urgency="medium",
                should_forward=False,
                apply_folder="Finance",
                mark_as_read=False,
                reasoning="Exceeds $500 threshold.",
            )

            mock_step1.invoke.return_value = header_finance
            mock_step2.invoke.return_value = finance_action

            action = classifier.classify_email(msg_finance)
            mock_step1.invoke.assert_called_once()
            mock_step2.invoke.assert_called_once()

            step2_prompt = mock_step2.invoke.call_args[0][0]
            # MUST contain finance rule
            self.assertIn("Custom finance prompt: Check if amount > $500.", step2_prompt)
            # MUST NOT contain travel rule
            self.assertNotIn("Custom travel prompt", step2_prompt)
            self.assertEqual(action.category, "finance")
        finally:
            if os.path.exists(rules_file):
                os.remove(rules_file)

    def test_explicit_required_outputs_in_prompt_and_action(self):
        custom_rules = [
            {
                "category": "travel",
                "prompt": "Verify flight confirmation.",
                "apply_folder": "Travel/Trips",
                "should_forward": True,
                "forward_to": "trips@assistant.com",
            }
        ]

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(custom_rules, f)
            rules_file = f.name

        try:
            msg = mock.MagicMock(
                from_="airline@sky.com",
                subject="Flight Confirmation",
                date_str="Mon, 28 Sep 2026",
                text="Confirmed flight.",
                html=None,
            )

            header_decision = HeaderClassification(
                category="travel",
                urgency="high",
                reasoning="Flight reservation.",
            )

            # LLM initially returned something divergent
            raw_action = EmailAction(
                category="travel",
                urgency="high",
                should_forward=False,
                apply_folder="Travel",
                mark_as_read=False,
                reasoning="Flight booked.",
            )

            mock_step1 = mock.MagicMock()
            mock_step2 = mock.MagicMock()
            mock_llm = mock.MagicMock()
            mock_llm.with_structured_output.side_effect = lambda schema: {
                HeaderClassification: mock_step1,
                EmailAction: mock_step2,
            }[schema]
            classifier = GeminiEmailClassifier(
                rules_file=rules_file,
                llm=mock_llm,
            )

            mock_step1.invoke.return_value = header_decision
            mock_step2.invoke.return_value = raw_action

            action = classifier.classify_email(msg)

            # Verify prompt explicitly instructed LLM on required outputs
            step2_prompt = mock_step2.invoke.call_args[0][0]
            self.assertIn("Explicit Required Outputs for this category:", step2_prompt)
            self.assertIn("- apply_folder: MUST be set to 'Travel/Trips'", step2_prompt)
            self.assertIn("- should_forward: MUST be set to true", step2_prompt)
            self.assertIn("- forward_to: MUST be set to 'trips@assistant.com'", step2_prompt)

            # Verify enforce_rule_outputs guaranteed the explicit outputs
            self.assertEqual(action.apply_folder, "Travel/Trips")
            self.assertTrue(action.should_forward)
            self.assertEqual(action.forward_to, "trips@assistant.com")
        finally:
            if os.path.exists(rules_file):
                os.remove(rules_file)

    def test_no_rules_configured_skips_llm(self):
        msg = mock.MagicMock(
            from_="anyone@example.com",
            subject="Hello World",
            date_str="Mon, 28 Sep 2026",
            text="Any body text here",
            html=None,
        )

        mock_llm = mock.MagicMock()
        classifier = GeminiEmailClassifier(llm=mock_llm)

        with mock.patch.object(classifier, "load_category_rules", return_value={}):
            action = classifier.classify_email(msg)

            # Neither Step 1 nor Step 2 must be invoked!
            mock_llm.with_structured_output.assert_not_called()

            self.assertEqual(action.category, "other")
            self.assertFalse(action.should_forward)
            self.assertIn("No classification rules configured", action.reasoning)

    def test_gemini_email_classifier_inheritance(self):
        """Verify that GeminiEmailClassifier is a subclass and instance of EmailClassifier."""
        self.assertTrue(issubclass(GeminiEmailClassifier, EmailClassifier))

        inst = GeminiEmailClassifier(llm=mock.MagicMock())
        self.assertIsInstance(inst, EmailClassifier)

    def test_gemini_email_classifier_init_missing_key(self):
        """Verify that initializing GeminiEmailClassifier without an API key raises ValueError."""
        with mock.patch.dict(os.environ, {}, clear=True):
            # Ensure no GEMINI_API_KEY or GOOGLE_API_KEY is present
            with self.assertRaises(ValueError) as ctx:
                GeminiEmailClassifier()
            self.assertIn("GEMINI_API_KEY or GOOGLE_API_KEY is required", str(ctx.exception))

    def test_gemini_email_classifier_custom_config(self):
        """Verify that custom constructor parameters properly configure GeminiEmailClassifier."""
        inst = GeminiEmailClassifier(
            model="custom-gemini-pro",
            rules_file="/tmp/custom_rules.json",
            mailbox_user="Alice",
            llm=mock.MagicMock(),
        )
        self.assertEqual(inst.mailbox_user, "Alice")
        self.assertEqual(inst.rules_file, "/tmp/custom_rules.json")
        self.assertEqual(inst.model, "custom-gemini-pro")

    def test_gemini_classifier_methods_use_prompt_keyword(self):
        """Verify Gemini classifier methods pass the prompt string to structured output."""
        mock_llm = mock.MagicMock()
        header_runnable = mock.MagicMock()
        action_runnable = mock.MagicMock()
        header_result = mock.MagicMock(spec=HeaderClassification)
        action_result = mock.MagicMock(spec=EmailAction)
        header_runnable.invoke.return_value = header_result
        action_runnable.invoke.return_value = action_result
        mock_llm.with_structured_output.side_effect = lambda schema: {
            HeaderClassification: header_runnable,
            EmailAction: action_runnable,
        }[schema]
        classifier = GeminiEmailClassifier(llm=mock_llm)

        self.assertIs(
            classifier.header_classifier(prompt="header prompt"),
            header_result,
        )
        self.assertIs(
            classifier.structured_classifier(prompt="action prompt"),
            action_result,
        )
        header_runnable.invoke.assert_called_once_with("header prompt")
        action_runnable.invoke.assert_called_once_with("action prompt")

    def test_gemini_email_classifier_inline_json_rules(self):
        """Verify loading category rules from CATEGORY_RULES_JSON environment variable."""
        rules_json = json.dumps([
            {"category": "security", "prompt": "Check 2FA security alerts."}
        ])
        with mock.patch.dict(os.environ, {"CATEGORY_RULES_JSON": rules_json}):
            classifier = GeminiEmailClassifier(llm=mock.MagicMock())
            rules = classifier.load_category_rules("/nonexistent/file.json")
            self.assertIn("security", rules)
            self.assertEqual(rules["security"]["prompt"], "Check 2FA security alerts.")

    def test_gemini_email_classifier_dict_rules_format(self):
        """Verify loading category rules formatted as a dictionary with 'rules' key or direct keys."""
        rules_dict = {
            "rules": [
                {"category": "shopping", "prompt": "Track orders."}
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(rules_dict, f)
            rules_file = f.name

        try:
            classifier = GeminiEmailClassifier(
                rules_file=rules_file,
                llm=mock.MagicMock(),
            )
            rules = classifier.load_category_rules()
            self.assertIn("shopping", rules)
            self.assertEqual(rules["shopping"]["prompt"], "Track orders.")
        finally:
            if os.path.exists(rules_file):
                os.remove(rules_file)

    def test_convenience_module_functions(self):
        """Verify that module-level convenience functions work properly."""
        msg = mock.MagicMock()
        msg.text = "Hello world"
        msg.html = None

        # 1. extract_email_text
        self.assertEqual(extract_email_text(msg), "Hello world")

        # 2. load_category_rules
        self.assertIsInstance(load_category_rules("/nonexistent.json"), dict)

        # 3. classify_email with mocked default instance
        mock_inst = mock.MagicMock(spec=GeminiEmailClassifier)
        mock_inst.classify_email.return_value = EmailAction(
            category="other",
            urgency="low",
            should_forward=False,
            apply_folder=None,
            mark_as_read=False,
            reasoning="Module function test",
        )
        with mock.patch(
            "gemini_classifier.GeminiEmailClassifier", return_value=mock_inst
        ):
            res = classify_email(msg)
            self.assertEqual(res.category, "other")
            mock_inst.classify_email.assert_called_once_with(msg, rules_path=None)

if __name__ == "__main__":
    unittest.main()
