from datetime import datetime, timezone
import unittest

from app.domain.models import VerificationClassification
from app.verification.engine import assess_evidence
from app.verification.models import VerificationEvidence


class VerificationTests(unittest.TestCase):
    def test_ats_listing_and_accessible_original_page_can_be_verified(self):
        result = assess_evidence(self.evidence())
        self.assertEqual(result.classification, VerificationClassification.VERIFIED)
        self.assertEqual(result.support_score, 90)
        self.assertEqual(result.score, 90)
        self.assertTrue(any("does not guarantee" in item for item in result.uncertainty))

    def test_missing_live_link_is_probable_and_missing_evidence_is_not_negative(self):
        result = assess_evidence(self.evidence(link_accessible=None))
        self.assertEqual(result.classification, VerificationClassification.PROBABLE)
        self.assertEqual(result.risk_score, 0)
        self.assertGreater(result.score, 0)
        self.assertTrue(any("live-link" in item for item in result.missing_evidence))

    def test_fee_request_is_a_risk_signal_and_suspicious(self):
        result = assess_evidence(self.evidence(description="Pay an upfront application fee to reserve this job."))
        self.assertEqual(result.classification, VerificationClassification.SUSPICIOUS)
        self.assertEqual(result.risk_score, 60)
        self.assertTrue(any(signal.name == "upfront_fee_request" for signal in result.signals))

    def test_explicit_statement_that_no_fee_is_required_is_not_flagged(self):
        result = assess_evidence(self.evidence(description="We never ask applicants to pay an application fee."))
        self.assertEqual(result.risk_score, 0)

    @staticmethod
    def evidence(**overrides):
        values = {
            "employer": "Acme Inc",
            "title": "Software Engineer",
            "original_url": "https://jobs.ashbyhq.com/acme/123",
            "source_url": "https://api.ashbyhq.com/posting-api/job-board/acme",
            "source_name": "ashby",
            "description": "Build and maintain software systems with a collaborative engineering team. Required skills include Python and PostgreSQL.",
            "posted_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
            "link_accessible": True,
            "observed_at": datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return VerificationEvidence(**values)


if __name__ == "__main__":
    unittest.main()
