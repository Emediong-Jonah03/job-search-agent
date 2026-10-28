from pathlib import Path

import yaml
from pydantic import ValidationError

from app.domain.models import Profile


class ProfileLoadError(ValueError):
    """Raised when the profile file cannot be parsed or validated."""


def load_profile(path: str | Path) -> Profile:
    profile_path = Path(path)
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileLoadError(f"Unable to read profile file: {profile_path}") from exc
    except yaml.YAMLError as exc:
        raise ProfileLoadError(f"Profile YAML is malformed: {profile_path}") from exc
    if not isinstance(data, dict):
        raise ProfileLoadError("Profile YAML must contain a mapping at its root")
    try:
        return Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileLoadError(str(exc)) from exc

