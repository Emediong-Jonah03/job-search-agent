import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.tracking.follow_ups import schedule_confirmed_submission


class FollowUpSchedulingTests(unittest.TestCase):
    def test_only_submitted_application_gets_a_configured_reminder(self):
        application_id = uuid4()
        submitted_at = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        session = Mock()
        session.scalar.side_effect = [SimpleNamespace(id=application_id, state="SUBMITTED"), None]
        with patch("app.tracking.follow_ups.get_settings", return_value=SimpleNamespace(follow_up_days=7)):
            reminder = schedule_confirmed_submission(session, application_id, submitted_at)
        self.assertEqual(reminder.scheduled_for, submitted_at + timedelta(days=7))
        self.assertEqual(reminder.status, "SCHEDULED")
        self.assertEqual(session.add.call_count, 2)  # reminder and audit event

    def test_unconfirmed_application_does_not_get_a_reminder(self):
        session = Mock()
        session.scalar.return_value = SimpleNamespace(state="APPROVED")
        self.assertIsNone(schedule_confirmed_submission(session, uuid4()))
        session.add.assert_not_called()

    def test_existing_reminder_makes_scheduling_idempotent(self):
        existing = object()
        session = Mock()
        session.scalar.side_effect = [SimpleNamespace(state="SUBMITTED"), existing]
        self.assertIs(schedule_confirmed_submission(session, uuid4()), existing)
        session.add.assert_not_called()
        session.flush.assert_not_called()


if __name__ == "__main__":
    unittest.main()
