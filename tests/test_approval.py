import unittest

from app.approval.policy import approval_blockers, approval_snapshot_matches, screening_answers_hash


class ApprovalPolicyTests(unittest.TestCase):
    def test_approval_requires_a_passed_document_check(self):
        blockers = approval_blockers(None, [], "VERIFIED")
        self.assertEqual(len(blockers), 1)
        self.assertIn("claim validation", blockers[0])

    def test_unresolved_screening_question_blocks_approval(self):
        blockers = approval_blockers({"passed": True}, ["RESOLVED", "NEEDS_CLARIFICATION"], "VERIFIED")
        self.assertTrue(any("screening question" in item for item in blockers))

    def test_review_passes_when_documents_and_all_questions_are_resolved(self):
        self.assertEqual(approval_blockers({"passed": True}, ["RESOLVED"], "VERIFIED"), [])

    def test_non_verified_posting_blocks_approval(self):
        blockers = approval_blockers({"passed": True}, [], "PROBABLE")
        self.assertTrue(any("not VERIFIED" in item for item in blockers))

    def test_answer_snapshot_changes_when_an_approved_answer_changes(self):
        first = [{"question_id": "q1", "status": "RESOLVED", "answer": "Canada", "profile_path": "work_authorization.countries[0]"}]
        changed = [{"question_id": "q1", "status": "RESOLVED", "answer": "Nigeria", "profile_path": "work_authorization.countries[1]"}]
        self.assertNotEqual(screening_answers_hash(first), screening_answers_hash(changed))

    def test_approval_rejects_stale_document_or_verification_snapshot(self):
        self.assertTrue(approval_snapshot_matches(2, 2, "v1", "v1", "a1", "a1"))
        self.assertFalse(approval_snapshot_matches(1, 2, "v1", "v1", "a1", "a1"))
        self.assertFalse(approval_snapshot_matches(2, 2, "v1", "v2", "a1", "a1"))
        self.assertFalse(approval_snapshot_matches(2, 2, "v1", "v1", "a1", "a2"))


if __name__ == "__main__":
    unittest.main()
