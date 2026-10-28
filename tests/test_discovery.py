from datetime import datetime, timedelta, timezone
import unittest

from app.discovery.adapters.ashby import AshbyAdapter
from app.discovery.adapters.greenhouse import GreenhouseAdapter
from app.discovery.adapters.lever import LeverAdapter
from app.discovery.models import DiscoveredJob
from app.discovery.normalization import normalize_url
from app.discovery.service import check_live_link, dedupe_key, fresh_jobs, parse_board_config
from app.domain.models import Profile
from app.matching.scorer import score_job


class DiscoveryTests(unittest.TestCase):
    def test_url_normalization_removes_tracking_and_keeps_job_id(self):
        normalized = normalize_url("HTTPS://WWW.Example.com/jobs/123/?utm_source=mail&gh_jid=123#top")
        self.assertEqual(normalized, "https://example.com/jobs/123?gh_jid=123")

    def test_job_identity_combines_normalized_url_employer_and_title(self):
        first = DiscoveredJob(employer="Acme, Inc.", title="Senior Engineer", original_url="https://jobs.example/1?utm_source=x", source_url="https://source.example", source_name="fixture", source_id="1")
        second = first.model_copy(update={"employer": "ACME Inc", "original_url": "https://jobs.example/1"})
        self.assertEqual(dedupe_key(first), dedupe_key(second))

    def test_only_dated_jobs_within_last_30_days_are_eligible(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        fresh = self.make_job(posted_at=now - timedelta(days=10))
        old = self.make_job(posted_at=now - timedelta(days=31))
        unknown = self.make_job(posted_at=None)
        eligible, excluded = fresh_jobs([fresh, old, unknown], now=now)
        self.assertEqual(eligible, [fresh])
        self.assertEqual(excluded, [old, unknown])

    def test_match_score_is_bounded_and_explained(self):
        profile = Profile.model_validate({
            "target_roles": ["Software Engineer"],
            "years_experience": 5,
            "skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
            "work_authorization": {"countries": ["United States"]},
            "work_preferences": {"arrangements": ["remote"]},
        })
        job = self.make_job(
            title="Remote Software Engineer",
            description="Python, PostgreSQL, 3 years experience",
            location="United States",
            workplace_type="Remote",
        )
        result = score_job(job, profile)
        self.assertGreaterEqual(result.score, 0)
        self.assertLessEqual(result.score, 100)
        self.assertIn("Skills", result.explanation)
        self.assertIn("Python", result.matched_requirements)

    def test_matcher_flags_explicit_deal_breaker_and_salary_currency_conflict(self):
        profile = Profile.model_validate({
            "salary_expectations": {"minimum": 100000, "currency": "USD"},
            "employment_preferences": {"deal_breakers": ["commission only"]},
        })
        job = self.make_job(description="Commission only role. Compensation: NGN 80,000-100,000", salary_text="NGN 80,000-100,000")
        result = score_job(job, profile)
        self.assertTrue(any("deal-breaker" in value for value in result.disqualifying_constraints))
        self.assertTrue(any("currency" in value for value in result.disqualifying_constraints))

    def test_board_config_accepts_supported_sources_and_rejects_unknown(self):
        configs = parse_board_config("greenhouse:acme; lever:acme-eu;ashby:Acme")
        self.assertEqual([item.source for item in configs], ["greenhouse", "lever", "ashby"])
        with self.assertRaises(ValueError):
            parse_board_config("linkedin:acme")

    def test_unknown_original_link_host_fails_closed(self):
        job = self.make_job(original_url="https://not-an-ats.example/apply")
        check = __import__("asyncio").run(check_live_link(job))
        self.assertFalse(check.accessible)
        self.assertIn("allowlist", check.reason)

    @staticmethod
    def make_job(**overrides):
        values = {
            "employer": "Acme",
            "title": "Engineer",
            "original_url": "https://jobs.ashbyhq.com/acme/abc",
            "source_url": "https://api.ashbyhq.com/posting-api/job-board/acme",
            "source_name": "ashby",
            "source_id": "abc",
        }
        values.update(overrides)
        return DiscoveredJob(**values)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_ashby_maps_explicit_publication_date_and_skips_unlisted(self):
        async def getter(url, **kwargs):
            return {"jobs": [
                {"title": "Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "applyUrl": "https://jobs.ashbyhq.com/acme/1/apply", "publishedAt": "2026-10-20T12:00:00Z", "descriptionPlain": "Python role", "isListed": True},
                {"title": "Private", "jobUrl": "https://jobs.ashbyhq.com/acme/2", "publishedAt": "2026-10-20T12:00:00Z", "isListed": False},
            ]}
        jobs = await AshbyAdapter(getter).fetch("acme")
        self.assertEqual(len(jobs), 1)
        self.assertIsNotNone(jobs[0].posted_at)

    async def test_greenhouse_uses_first_published_but_not_updated_at_and_lever_date_stays_unknown(self):
        async def greenhouse_getter(url, **kwargs):
            return {"jobs": [
                {"id": 7, "internal_job_id": 9, "title": "Engineer", "company_name": "Acme Inc", "absolute_url": "https://boards.greenhouse.io/acme/jobs/7", "updated_at": "2026-10-20T12:00:00Z", "first_published": "2026-10-21T12:00:00Z", "content": "Python"},
                {"id": 8, "internal_job_id": 10, "title": "Engineer 2", "absolute_url": "https://boards.greenhouse.io/acme/jobs/8", "updated_at": "2026-10-21T12:00:00Z", "content": "Python"},
            ]}
        async def lever_getter(url, **kwargs):
            return [{"id": "abc", "text": "Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc", "descriptionPlain": "Python"}]
        greenhouse = await GreenhouseAdapter(greenhouse_getter).fetch("acme")
        lever = await LeverAdapter(lever_getter).fetch("acme")
        self.assertEqual(greenhouse[0].employer, "Acme Inc")
        self.assertIsNotNone(greenhouse[0].posted_at)
        self.assertIsNone(greenhouse[1].posted_at)
        self.assertIsNone(lever[0].posted_at)

