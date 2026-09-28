import tempfile
import os
import unittest
from fastapi.testclient import TestClient

import db
from schemas import EmailAction
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
            reasoning="Flight reservation"
        )
        db.record_audit(
            uid="1",
            sender="airline@test.com",
            subject="Flight Confirmation NYC-LON",
            action=act1,
            executed_summary="forwarded:fwd@test.com, moved:Travel, marked_read",
            is_dry_run=False,
            db_path=self.db_path
        )

        # Record 2: Finance (Live)
        act2 = EmailAction(
            category="finance",
            urgency="low",
            should_forward=False,
            apply_folder="Finance",
            mark_as_read=False,
            reasoning="Monthly phone bill"
        )
        db.record_audit(
            uid="2",
            sender="telco@test.com",
            subject="Monthly Statement Sept 2026",
            action=act2,
            executed_summary="moved:Finance",
            is_dry_run=False,
            db_path=self.db_path
        )

        # Record 3: Newsletter (Live)
        act3 = EmailAction(
            category="newsletter",
            urgency="low",
            should_forward=False,
            apply_folder="Newsletters",
            mark_as_read=True,
            reasoning="Weekly newsletter"
        )
        db.record_audit(
            uid="3",
            sender="news@digest.com",
            subject="Weekly AI Roundup",
            action=act3,
            executed_summary="moved:Newsletters, marked_read",
            is_dry_run=False,
            db_path=self.db_path
        )

        # Record 4: Travel (Dry-Run)
        act4 = EmailAction(
            category="travel",
            urgency="medium",
            should_forward=True,
            forward_to="fwd@test.com",
            apply_folder="Travel",
            mark_as_read=True,
            reasoning="Hotel booking"
        )
        db.record_audit(
            uid="4",
            sender="hotel@booking.com",
            subject="Hotel reservation confirmation",
            action=act4,
            executed_summary="[dry-run] would_forward:fwd@test.com, would_move:Travel, would_mark_read",
            is_dry_run=True,
            db_path=self.db_path
        )

        # Record 5: Other (Dry-Run)
        act5 = EmailAction(
            category="other",
            urgency="low",
            should_forward=False,
            apply_folder=None,
            mark_as_read=False,
            reasoning="Lunch invite"
        )
        db.record_audit(
            uid="5",
            sender="friend@domain.com",
            subject="Lunch tomorrow?",
            action=act5,
            executed_summary="[dry-run] no_action",
            is_dry_run=True,
            db_path=self.db_path
        )

    def test_uid_processed_tracking(self):
        # Live UID 1 is processed
        self.assertTrue(db.is_uid_processed("1", is_dry_run=False, db_path=self.db_path))
        # Live UID 1 was not processed as dry-run
        self.assertFalse(db.is_uid_processed("1", is_dry_run=True, db_path=self.db_path))
        # Dry-run UID 4 is processed
        self.assertTrue(db.is_uid_processed("4", is_dry_run=True, db_path=self.db_path))
        # Dry-run UID 4 was not processed as live
        self.assertFalse(db.is_uid_processed("4", is_dry_run=False, db_path=self.db_path))

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
        import unittest.mock as mock
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
            reasoning="Flight reservation detected."
        )

        with mock.patch("daemon.classify_email", return_value=mock_decision), \
             mock.patch("daemon.forward_message") as mock_fwd:
            
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
            self.assertEqual(live_item["actions_executed"], "forwarded:test-fwd@example.com, moved:Travel, marked_read")

if __name__ == "__main__":
    unittest.main()
