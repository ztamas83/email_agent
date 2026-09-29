import tempfile
import os
import unittest
import unittest.mock as mock
from fastapi.testclient import TestClient

import db
import daemon
from schemas import EmailAction
from email_classifier import EmailClassifier
from web import app

class TestDaemon(unittest.TestCase):
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

    def test_daemon_dry_run_and_live_integration(self):
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
        mock_mailbox = mock.MagicMock()
        mock_mailbox.uids.return_value = ["1", "5", "42", "7"]
        self.assertEqual(daemon.get_max_uid(mock_mailbox), 42)

        mock_mailbox.uids.return_value = []
        self.assertEqual(daemon.get_max_uid(mock_mailbox), 0)

if __name__ == "__main__":
    unittest.main()
