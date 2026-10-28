import unittest
from datetime import datetime, timezone

from app.tracking.drafts import build_follow_up_draft


class FollowUpDraftTests(unittest.TestCase):
    def test_draft_uses_confirmed_job_and_submission_facts(self):
        draft = build_follow_up_draft(
            "Example Company", "Backend Engineer",
            datetime(2026, 10, 28, 12, tzinfo=timezone.utc), "Ada Lovelace",
        )
        self.assertIn("Backend Engineer", draft)
        self.assertIn("Example Company", draft)
        self.assertIn("2026-10-28", draft)
        self.assertTrue(draft.endswith("Best,\nAda Lovelace"))
        self.assertNotIn("years of experience", draft.lower())

    def test_missing_name_is_shown_as_a_placeholder(self):
        draft = build_follow_up_draft(
            "Example Company", "Backend Engineer",
            datetime(2026, 10, 28, 12, tzinfo=timezone.utc), "[Your name]",
        )
        self.assertTrue(draft.endswith("Best,\n[Your name]"))


if __name__ == "__main__":
    unittest.main()
