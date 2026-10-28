import unittest

from app.documents.builder import build_document_content
from app.documents.claim_validation import validate_document_claims
from app.documents.rendering import cv_plain_text, render_document_files
from app.domain.models import Profile
from app.discovery.models import DiscoveredJob
from app.llm.models import JobDescriptionAnalysis


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile.model_validate({
            "full_name": "Jordan Example",
            "contact": {"email": "jordan@example.com"},
            "skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
            "work_history": [{
                "employer": "Example Systems", "title": "Software Engineer",
                "start_date": "2022", "end_date": "Present",
                "responsibilities": ["Built Python services for customer workflows."],
            }],
            "projects": [{
                "name": "Service Toolkit", "description": "A small toolkit for API operations.",
                "technologies": ["Python"], "results": ["Reduced manual release steps."],
            }],
        })
        self.job = DiscoveredJob(
            employer="Example Corp", title="Backend Engineer",
            original_url="https://jobs.example.com/1", source_url="https://jobs.example.com/",
            description="Python and PostgreSQL required.", source_name="test", source_id="1",
        )
        self.analysis = JobDescriptionAnalysis(required_skills=["Python"], keywords=["Python", "PostgreSQL"])

    def test_generated_claims_resolve_to_exact_profile_facts(self):
        content = build_document_content(self.profile, self.job, self.analysis)
        report = validate_document_claims(self.profile, content, cv_plain_text(content.cv))
        self.assertTrue(report.passed, report.unsupported_claims)
        self.assertLess(content.cover_letter_word_count, 250)

    def test_modified_personal_claim_is_rejected(self):
        content = build_document_content(self.profile, self.job, self.analysis)
        changed = content.cv.model_copy(update={
            "full_name": content.cv.full_name.model_copy(update={"text": "Jordan Expert"})
        })
        content = content.model_copy(update={"cv": changed})
        report = validate_document_claims(self.profile, content, "Jordan Expert")
        self.assertFalse(report.passed)
        self.assertTrue(any("differs from the profile" in item for item in report.unsupported_claims))

    def test_renders_readable_pdf_and_docx_files(self):
        content = build_document_content(self.profile, self.job, self.analysis)
        files, extracted, _ = render_document_files(self.profile, content)
        self.assertEqual(set(files), {"cv.pdf", "cv.docx", "cover-letter.pdf", "cover-letter.docx"})
        for filename, text in extracted.items():
            self.assertTrue(text.strip(), filename)
        self.assertIn("Jordan Example", extracted["cv.docx"])
        self.assertIn("Backend Engineer", extracted["cover-letter.pdf"])


if __name__ == "__main__":
    unittest.main()
