"""Load the hand-maintained profile: the source of truth for personal facts."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ProfileEntry(BaseModel):
    """A work or education record with a stable identity, otherwise free-form."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)


class Profile(BaseModel):
    """Structured personal facts and the configured resume file."""

    model_config = ConfigDict(extra="forbid")

    resume_path: Path
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    website: str | None = None
    linkedin: str | None = None
    work_history: list[ProfileEntry] = Field(default_factory=list)
    education: list[ProfileEntry] = Field(default_factory=list)
    skills: list[Any] | dict[str, Any] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(default_factory=dict)
    # Sensitive or legally significant answers (work authorization, sponsorship,
    # disability, veteran status, criminal history, ...). Only an explicit key here
    # may answer a sensitive question; a missing key must never be inferred.
    sensitive_answers: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def entry_ids_are_unique(self) -> "Profile":
        for section in ("work_history", "education"):
            ids = [entry.id for entry in getattr(self, section)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{section} entry IDs must be unique")
        return self


def load_profile(path: Path) -> Profile:
    """Load and validate YAML profile data and resolve its resume path."""

    path = Path(path)
    try:
        with path.open(encoding="utf-8") as profile_file:
            data = yaml.safe_load(profile_file)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Profile file does not exist: {path}") from exc
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"Unable to parse YAML profile {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Profile {path} must contain a YAML mapping")

    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid profile {path}: {exc}") from exc

    if not profile.resume_path.is_absolute():
        profile.resume_path = (path.parent / profile.resume_path).resolve()
    if not profile.resume_path.is_file():
        raise FileNotFoundError(f"Configured resume file does not exist: {profile.resume_path}")
    return profile
