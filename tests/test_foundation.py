from datetime import date
import unittest

from app.audit.redaction import redact
from app.domain.models import ApplicationState, Profile
from app.domain.state_machine import InvalidTransition, transition
from pydantic import ValidationError


class FoundationTests(unittest.TestCase):
    def test_valid_state_transition(self):
        item = transition(ApplicationState.DISCOVERED, ApplicationState.VERIFIED, "system")
        self.assertEqual(item.previous, ApplicationState.DISCOVERED)
        self.assertEqual(item.new, ApplicationState.VERIFIED)

    def test_invalid_state_transition_is_rejected(self):
        with self.assertRaises(InvalidTransition):
            transition(ApplicationState.DISCOVERED, ApplicationState.SUBMITTED, "system")

    def test_sensitive_audit_data_is_redacted(self):
        clean = redact({"api_key": "secret-value", "contact": "person@example.com", "detail": "safe"})
        self.assertEqual(clean["api_key"], "[REDACTED]")
        self.assertNotIn("person@example.com", str(clean))
        self.assertEqual(clean["detail"], "safe")

    def test_empty_profile_template_is_valid_and_typed(self):
        profile = Profile.model_validate({})
        self.assertEqual(profile.full_name, "")
        self.assertEqual(profile.work_authorization.countries, [])

    def test_malformed_profile_data_is_rejected(self):
        with self.assertRaises(ValidationError):
            Profile.model_validate({"years_experience": -1})

    def test_profile_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            Profile.model_validate({"invented_field": "value"})


if __name__ == "__main__":
    unittest.main()

