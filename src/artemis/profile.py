"""Load the hand-maintained profile: the source of truth for personal facts."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ProfileEntry(BaseModel):
    """A work or education record with a stable identity, otherwise free-form."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    title: str | None = None
    organization: str | None = None
    start: str | None = None
    end: str | None = None
    # 1-3 sentences: the text an LLM drafter cites as grounding for this entry.
    summary: str | None = None


class Preferences(BaseModel):
    """Explicit, typed, never-inferred answers to recurring application
    questions that aren't on the resume and aren't protected (see `declared`
    on `Profile`) -- what you'd tell every application about your logistics
    and terms, in your own words, once."""

    model_config = ConfigDict(extra="allow")

    # str, not int/date: forms take free text ("negotiable", "150-170k",
    # "after March") and typing these numerically forces a lossy conversion
    # exactly where precision matters.
    salary_expectation: str | None = None
    salary_currency: str | None = None
    desired_start_date: str | None = None
    notice_period: str | None = None
    # None must stay distinguishable from False: unset falls through to a
    # live prompt, False is an explicit "no".
    willing_to_relocate: bool | None = None
    relocation_notes: str | None = None
    remote_preference: Literal["remote", "hybrid", "onsite", "flexible"] | None = None
    onsite_days_per_week: int | None = None
    locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    referral_source: str | None = None
    how_heard: str | None = None
    years_experience: str | None = None


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
    preferences: Preferences = Field(default_factory=Preferences)
    # Free-text overview/summary, usually lifted from the resume itself
    # (backward-looking: who you are professionally).
    background: str | None = None
    # Free-text context for drafting that isn't on the resume: what kind of
    # role/company you're looking for next, in your own words (forward-looking).
    goals: str | None = None
    # Protected or legally significant facts (work authorization, sponsorship,
    # disability, veteran status, criminal history, demographics, ...), declared
    # explicitly by hand. Fills and submits automatically like any other profile
    # field, but is never derived: never inferred from another field, never
    # matched across categories, never learned from a live prompt, never drafted.
    # A protected question with no key here is always deferred, never guessed.
    declared: dict[str, Any] = Field(default_factory=dict)

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
