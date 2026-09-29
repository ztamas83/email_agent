import tempfile
import os
import json
import unittest
import unittest.mock as mock
from fastapi.testclient import TestClient

import db
from email_classifier import EmailClassifier
from gemini_classifier import (
    GeminiEmailClassifier,
    extract_email_text,
    load_category_rules,
    classify_email,
)
from jev_classifier import JevEmailClassifier
from schemas import EmailAction, HeaderClassification
from web import app


class TestTriageSystem(unittest.TestCase):
    def setUp(self):
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db_file.name
        self.temp_db_file.close()

        # Point db.DB_PATH to temporary test database
        self.orig_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db.init_db(self.db_path)

        # Seed test data: 3 live, 2 dry-run records
        self._seed_data()
        self.client = TestClient(app)

    def tearDown(self):
        db.DB_PATH = self.orig_db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_data(self):
        # Record 1: Travel (Live)
        act1 = EmailAction(
            category="travel",
            urgency="high",
            should_forward=True,
            forward_to="fwd@test.com",
            apply_folder="Travel",
            mark_as_read=True,
            reasoning="Flight reservation",
        )
        db.record_audit(
            uid="1",
            sender="airline@test.com",
            subject="Flight Confirmation NYC-LON",
            action=act1,
            executed_summary="forwarded:fwd@test.com, moved:Travel, marked_read",
            is_dry_run=False,
            db_path=self.db_path,
        )

        # Record 2: Finance (Live)
        act2 = EmailAction(
            category="finance",
            urgency="low",
            should_forward=False,
            apply_folder="Finance",
            mark_as_read=False,
            reasoning="Monthly phone bill",
        )
        db.record_audit(
            uid="2",
            sender="telco@test.com",
            subject="Monthly Statement Sept 2026",
            action=act2,
            executed_summary="moved:Finance",
            is_dry_run=False,
            db_path=self.db_path,
        )

        # Record 3: Newsletter (Live)
        act3 = EmailAction(
            category="newsletter",
            urgency="low",
            should_forward=False,
            apply_folder="Newsletters",
            mark_as_read=True,
            reasoning="Weekly newsletter",
        )
        db.record_audit(
            uid="3",
            sender="news@digest.com",
            subject="Weekly AI Roundup",
            action=act3,
            executed_summary="moved:Newsletters, marked_read",
            is_dry_run=False,
            db_path=self.db_path,
        )

        # Record 4: Travel (Dry-Run)
        act4 = EmailAction(
            category="travel",
            urgency="medium",
            should_forward=True,
            forward_to="fwd@test.com",
            apply_folder="Travel",
            mark_as_read=True,
            reasoning="Hotel booking",
        )
        db.record_audit(
            uid="4",
            sender="hotel@booking.com",
            subject="Hotel reservation confirmation",
            action=act4,
            executed_summary="[dry-run] would_forward:fwd@test.com, would_move:Travel, would_mark_read",
            is_dry_run=True,
            db_path=self.db_path,
        )

        # Record 5: Other (Dry-Run)
        act5 = EmailAction(
            category="other",
            urgency="low",
            should_forward=False,
            apply_folder=None,
            mark_as_read=False,
            reasoning="Lunch invite",
        )
        db.record_audit(
            uid="5",
            sender="friend@domain.com",
            subject="Lunch tomorrow?",
            action=act5,
            executed_summary="[dry-run] no_action",
            is_dry_run=True,
            db_path=self.db_path,
        )

    def test_uid_processed_tracking(self):
        # Live UID 1 is processed
        self.assertTrue(
            db.is_uid_processed("1", is_dry_run=False, db_path=self.db_path)
        )
        # Live UID 1 was not processed as dry-run
        self.assertFalse(
            db.is_uid_processed("1", is_dry_run=True, db_path=self.db_path)
        )
        # Dry-run UID 4 is processed
        self.assertTrue(
            db.is_uid_processed("4", is_dry_run=True, db_path=self.db_path)
        )
        # Dry-run UID 4 was not processed as live
        self.assertFalse(
            db.is_uid_processed("4", is_dry_run=False, db_path=self.db_path)
        )

    def test_web_index(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Email Triage Audit Logs", res.text)
        self.assertIn("text/html", res.headers["content-type"])

    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_api_stats(self):
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["live"], 3)
        self.assertEqual(data["dry_run"], 2)
        self.assertEqual(data["categories"]["travel"], 2)
        self.assertEqual(data["categories"]["finance"], 1)

    def test_api_logs_pagination(self):
        # Page size 2
        res = self.client.get("/api/logs?page=1&page_size=2")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 2)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(len(data["items"]), 2)

        # Page 2
        res2 = self.client.get("/api/logs?page=2&page_size=2")
        data2 = res2.json()
        self.assertEqual(data2["page"], 2)
        self.assertEqual(len(data2["items"]), 2)
        self.assertNotEqual(data["items"][0]["id"], data2["items"][0]["id"])

    def test_api_logs_filter_category(self):
        res = self.client.get("/api/logs?category=travel")
        data = res.json()
        self.assertEqual(data["total"], 2)
        for item in data["items"]:
            self.assertEqual(item["category"], "travel")

    def test_api_logs_filter_mode(self):
        # Live only
        res_live = self.client.get("/api/logs?mode=live")
        data_live = res_live.json()
        self.assertEqual(data_live["total"], 3)
        for item in data_live["items"]:
            self.assertEqual(item["is_dry_run"], 0)

        # Dry run only
        res_dry = self.client.get("/api/logs?mode=dryrun")
        data_dry = res_dry.json()
        self.assertEqual(data_dry["total"], 2)
        for item in data_dry["items"]:
            self.assertEqual(item["is_dry_run"], 1)

    def test_api_logs_filter_search(self):
        res = self.client.get("/api/logs?search=Roundup")
        data = res.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["subject"], "Weekly AI Roundup")

    def test_api_logs_filter_date(self):
        import datetime

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        res = self.client.get(f"/api/logs?start_date={today_str}&end_date={today_str}")
        data = res.json()
        self.assertEqual(data["total"], 5)

        # Future date should return 0
        res_future = self.client.get("/api/logs?start_date=2099-01-01")
        data_future = res_future.json()
        self.assertEqual(data_future["total"], 0)

    def test_daemon_dry_run_and_live_integration(self):
        import daemon

        mock_mailbox = mock.MagicMock()
        mock_msg = mock.MagicMock()
        mock_msg.uid = "integration-999"
        mock_msg.subject = "Flight Itinerary for Test"
        mock_msg.from_ = "bookings@airlines.com"

        mock_decision = EmailAction(
            category="travel",
            urgency="high",
            should_forward=True,
            forward_to="test-fwd@example.com",
            apply_folder="Travel",
            mark_as_read=True,
            reasoning="Flight reservation detected.",
        )

        mock_classifier = mock.MagicMock(spec=EmailClassifier)
        mock_classifier.classify_email.return_value = mock_decision

        with mock.patch(
            "daemon.GeminiEmailClassifier", return_value=mock_classifier
        ), mock.patch(
            "daemon.JevEmailClassifier", return_value=mock_classifier
        ), mock.patch(
            "daemon.forward_message"
        ) as mock_fwd:

            # 1. Process in DRY RUN mode
            daemon.process_message(mock_mailbox, mock_msg, dry_run=True)

            # Assert NO mutations were executed
            mock_fwd.assert_not_called()
            mock_mailbox.move.assert_not_called()
            mock_mailbox.flag.assert_not_called()

            # Verify dry-run audit entry exists in API
            res = self.client.get("/api/logs?mode=dryrun&search=integration-999")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["total"], 1)
            item = data["items"][0]
            self.assertEqual(item["is_dry_run"], 1)
            self.assertIn("[dry-run]", item["actions_executed"])
            self.assertIn("would_forward:test-fwd@example.com", item["actions_executed"])
            self.assertIn("would_move:Travel", item["actions_executed"])
            self.assertIn("would_mark_read", item["actions_executed"])

            # 2. Second dry-run call with same UID -> should be skipped!
            daemon.process_message(mock_mailbox, mock_msg, dry_run=True)
            res2 = self.client.get("/api/logs?mode=dryrun&search=integration-999")
            self.assertEqual(res2.json()["total"], 1)

            # 3. Live processing for this message
            mock_fwd.return_value = "test-fwd@example.com"
            daemon.process_message(mock_mailbox, mock_msg, dry_run=False)

            # Assert mutations WERE executed in live mode
            mock_fwd.assert_called_once()
            mock_mailbox.move.assert_called_once_with("integration-999", "Travel")
            mock_mailbox.flag.assert_called_once_with("integration-999", r"\Seen", True)

            # Verify live entry exists in API
            res_live = self.client.get("/api/logs?mode=live&search=integration-999")
            self.assertEqual(res_live.json()["total"], 1)
            live_item = res_live.json()["items"][0]
            self.assertEqual(live_item["is_dry_run"], 0)
            self.assertEqual(
                live_item["actions_executed"],
                "forwarded:test-fwd@example.com, moved:Travel, marked_read",
            )

    def test_daemon_custom_classifier_injection(self):
        import daemon

        mock_mailbox = mock.MagicMock()
        mock_msg = mock.MagicMock()
        mock_msg.uid = "custom-classifier-1"
        mock_msg.subject = "Custom Classifier Test"
        mock_msg.from_ = "sender@custom.com"

        custom_action = EmailAction(
            category="finance",
            urgency="high",
            should_forward=False,
            apply_folder="Finance/Custom",
            mark_as_read=True,
            reasoning="Custom classifier processed this",
        )

        class CustomClassifier(EmailClassifier):
            def classify_email(self, msg, rules_path=None):
                return custom_action

            def header_classifier(self, **kwargs):
                pass

            def structured_classifier(self, **kwargs):
                pass

        custom_classifier = CustomClassifier()
        daemon.process_message(
            mock_mailbox, mock_msg, dry_run=True, classifier=custom_classifier
        )

        res = self.client.get("/api/logs?search=custom-classifier-1")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["category"], "finance")
        self.assertIn("would_move:Finance/Custom", data["items"][0]["actions_executed"])

    def test_connect_mailbox_auto_fallback(self):
        import ssl
        import daemon

        mock_mb_starttls = mock.MagicMock()
        mock_mb_ssl = mock.MagicMock()

        # 1. On port 1143 with auto: STARTTLS is preferred and succeeds
        with mock.patch("daemon.MailBoxStartTls", return_value=mock_mb_starttls) as mock_st_cls, \
             mock.patch("daemon.MailBox", return_value=mock_mb_ssl):
            mb, mode = daemon.connect_mailbox(
                host="127.0.0.1", port=1143, user="u", pass_="p", security="auto"
            )
            self.assertEqual(mode, "STARTTLS")
            mock_st_cls.assert_called_once()
            mock_mb_starttls.login.assert_called_once_with("u", "p", "INBOX")

        # 2. When STARTTLS fails with SSLError, falls back to direct SSL/TLS
        with mock.patch("daemon.MailBoxStartTls", side_effect=ssl.SSLError("wrong version number")), \
             mock.patch("daemon.MailBox", return_value=mock_mb_ssl) as mock_ssl_cls:
            mb, mode = daemon.connect_mailbox(
                host="127.0.0.1", port=1143, user="u", pass_="p", security="auto"
            )
            self.assertEqual(mode, "SSL/TLS")
            mock_ssl_cls.assert_called_once()
            mock_mb_ssl.login.assert_called_once_with("u", "p", "INBOX")

        # 3. Explicit security=starttls
        with mock.patch("daemon.MailBoxStartTls", return_value=mock_mb_starttls) as mock_st_cls:
            mb, mode = daemon.connect_mailbox(
                host="127.0.0.1", port=1143, user="u", pass_="p", security="starttls"
            )
            self.assertEqual(mode, "STARTTLS")

    def test_history_days_filtering(self):
        import datetime
        import daemon

        mock_mailbox = mock.MagicMock()

        # Create 3 mock messages:
        # msg1: UID 10, 10 days ago (old)
        # msg2: UID 20, 2 days ago (recent)
        # msg3: UID 30, today (new)
        today = datetime.datetime.now(datetime.timezone.utc)

        msg1 = mock.MagicMock(uid="10", date=today - datetime.timedelta(days=10))
        msg2 = mock.MagicMock(uid="20", date=today - datetime.timedelta(days=2))
        msg3 = mock.MagicMock(uid="30", date=today)

        mock_mailbox.fetch.return_value = [msg3, msg2, msg1]

        # Case 1: HISTORY_DAYS=0 with baseline min_uid=20
        with mock.patch("daemon.process_message") as mock_proc:
            daemon.drain_unread(mock_mailbox, history_days=0, min_uid=20)
            self.assertEqual(mock_proc.call_count, 1)
            self.assertEqual(mock_proc.call_args[0][1].uid, "30")

        # Case 2: HISTORY_DAYS=5
        with mock.patch("daemon.process_message") as mock_proc:
            daemon.drain_unread(mock_mailbox, history_days=5, min_uid=None)
            self.assertEqual(mock_proc.call_count, 2)
            processed_uids = [call[0][1].uid for call in mock_proc.call_args_list]
            self.assertIn("30", processed_uids)
            self.assertIn("20", processed_uids)
            self.assertNotIn("10", processed_uids)

        # Case 3: HISTORY_DAYS=None (all history)
        with mock.patch("daemon.process_message") as mock_proc:
            daemon.drain_unread(mock_mailbox, history_days=None, min_uid=None)
            self.assertEqual(mock_proc.call_count, 3)

    def test_get_max_uid(self):
        import daemon

        mock_mailbox = mock.MagicMock()
        mock_mailbox.uids.return_value = ["1", "5", "42", "7"]
        self.assertEqual(daemon.get_max_uid(mock_mailbox), 42)

        mock_mailbox.uids.return_value = []
        self.assertEqual(daemon.get_max_uid(mock_mailbox), 0)

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


class TestClassifierArchitecture(unittest.TestCase):
    """Dedicated test suite validating the EmailClassifier interface and GeminiEmailClassifier implementation."""

    def test_email_classifier_abc_cannot_be_instantiated(self):
        """Verify that EmailClassifier is an ABC and cannot be directly instantiated."""
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
