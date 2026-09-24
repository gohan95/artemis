"""Load trusted profile data and resume text as source-backed evidence."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import pypdf


class ProfileEntry(BaseModel):
    """A human-maintained work or education record with a stable identity."""

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
    sensitive_answers: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def entry_ids_are_unique(self) -> "Profile":
        for section in ("work_history", "education"):
            ids = [entry.id for entry in getattr(self, section)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{section} entry IDs must be unique")
        return self


class ResumeFact(BaseModel):
    """Text extracted from one resume page."""

    id: str
    value: str
    source: str = "resume"


class EvidenceFact(BaseModel):
    """A string-valued claim with a stable ID and source label."""

    id: str
    value: str
    source: str


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


def _serialize(value: Any) -> str:
    """Convert structured profile values to deterministic evidence strings."""

    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_serialize(item)}"
            for key, item in sorted(value.items())
            if item is not None
        )
    if isinstance(value, (list, tuple)):
        return "; ".join(_serialize(item) for item in value if item is not None)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_evidence(profile: Profile) -> list[EvidenceFact]:
    """Create stable profile and resume evidence without inferring missing facts."""

    facts: list[EvidenceFact] = []

    def add(identifier: str, value: Any, source: str = "profile") -> None:
        if value is None:
            return
        rendered = _serialize(value)
        if rendered:
            facts.append(EvidenceFact(id=identifier, value=rendered, source=source))

    for field in ("full_name", "email", "phone", "location", "website", "linkedin"):
        add(f"profile.{field}", getattr(profile, field))
    for section in ("work_history", "education"):
        for entry in getattr(profile, section):
            for key, value in sorted(entry.model_dump().items()):
                add(f"profile.{section}.{entry.id}.{key}", value)
    add("profile.skills", profile.skills)
    for field, value in sorted(profile.preferences.items()):
        add(f"profile.preferences.{field}", value)
    for field, value in sorted(profile.sensitive_answers.items()):
        add(f"profile.sensitive_answers.{field}", value)

    try:
        reader = pypdf.PdfReader(str(profile.resume_path))
        resume_facts = [
            ResumeFact(id=f"resume.page.{number}", value=page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except Exception as exc:
        raise ValueError(f"Unable to extract text from resume PDF {profile.resume_path}: {exc}") from exc

    facts.extend(
        EvidenceFact(id=fact.id, value=fact.value, source=fact.source)
        for fact in resume_facts
        if fact.value.strip()
    )
    return facts
