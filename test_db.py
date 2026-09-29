import tempfile
import os
import unittest

import db
from schemas import EmailAction

class TestDatabase(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
