import tempfile
import os
import json
import unittest
from unittest import mock
from fastapi.testclient import TestClient

import db
import web
from schemas import EmailAction
from web import app

class TestWebAPI(unittest.TestCase):
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
        
        # Isolate web rules file
        self.temp_rules_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_rules_file.close()
        initial_rules = [
            {
                "category": "travel",
                "category_criteria": "Tickets",
                "prompt": "Travel prompt",
                "apply_folder": "Travel",
                "should_forward": True,
                "forward_to": "sample@email.com",
                "mark_as_read": False
            }
        ]
        with open(self.temp_rules_file.name, "w", encoding="utf-8") as f:
            json.dump(initial_rules, f)
            
        self.orig_rules_file = web.RULES_FILE
        web.RULES_FILE = self.temp_rules_file.name

        self.client = TestClient(app)

    def tearDown(self):
        db.DB_PATH = self.orig_db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
            
        web.RULES_FILE = self.orig_rules_file
        if os.path.exists(self.temp_rules_file.name):
            os.remove(self.temp_rules_file.name)

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

    def test_api_get_rules(self):
        res = self.client.get("/api/rules")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["category"], "travel")
        self.assertEqual(data[0]["category_criteria"], "Tickets")
        self.assertTrue(data[0]["should_forward"])

    def test_api_save_rules_valid(self):
        new_rules = [
            {
                "category": "finance",
                "category_criteria": "Bills",
                "prompt": "Finance prompt",
                "apply_folder": "Finance",
                "should_forward": False,
                "forward_to": "",
                "mark_as_read": True
            }
        ]
        res = self.client.put("/api/rules", json=new_rules)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["rules"]), 1)
        self.assertEqual(data["rules"][0]["category"], "finance")
        self.assertFalse(data["rules"][0]["should_forward"])
        self.assertTrue(data["rules"][0]["mark_as_read"])

    def test_api_save_rules_invalid_missing_fields(self):
        # Missing apply_folder
        invalid_rules = [
            {
                "category": "finance",
                "category_criteria": "Bills",
                "prompt": "Finance prompt",
                "apply_folder": "",
                "should_forward": False,
                "forward_to": ""
            }
        ]
        res = self.client.put("/api/rules", json=invalid_rules)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Apply folder is required", res.json()["detail"])

    @mock.patch("daemon.connect_mailbox")
    @mock.patch("daemon.process_message")
    def test_api_reprocess_message_success(self, mock_process, mock_connect):
        mock_mailbox = mock.MagicMock()
        mock_msg = mock.MagicMock()
        mock_msg.uid = "123"
        mock_mailbox.fetch.return_value = [mock_msg]
        mock_connect.return_value = (mock_mailbox, "SSL/TLS")

        # Call the endpoint
        res = self.client.post("/api/reprocess/123?dry_run=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("reprocessed successfully", data["message"])
        self.assertTrue(data["dry_run"])

        mock_connect.assert_called_once()
        mock_process.assert_called_once_with(
            mock_mailbox, mock_msg, dry_run=True, classifier=mock.ANY, force=True
        )
        mock_mailbox.logout.assert_called_once()

    @mock.patch("daemon.connect_mailbox")
    def test_api_reprocess_message_not_found(self, mock_connect):
        mock_mailbox = mock.MagicMock()
        mock_mailbox.fetch.return_value = []
        mock_connect.return_value = (mock_mailbox, "SSL/TLS")

        # Call the endpoint
        res = self.client.post("/api/reprocess/999?dry_run=true")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found in mailbox", res.json()["detail"])
        mock_mailbox.logout.assert_called_once()

if __name__ == "__main__":
    unittest.main()
