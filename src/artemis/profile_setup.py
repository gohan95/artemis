"""Pure helpers for the `artemis setup` command: resume parsing, LLM extraction,
merging into a Profile, and writing it back out.

No interactive prompting lives here -- cli.py owns that so this module stays
testable without a live API key or a resume file on disk. The one exception is
printing the cause of an LLM failure to stderr: swallowing it silently made
real problems (bad key, wrong model name, network) undiagnosable.
"""

import re
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from artemis.llm import LLMClient, LLMError
from artemis.profile import Profile, ProfileEntry

_PROFILE_HEADER = (
    "# Required: resume_path must point to an existing, readable PDF. "
    "All other fields are optional.\n"
)


class _ExtractedEntry(BaseModel):
    title: str | None = None
    organization: str | None = None
    start: str | None = None
    end: str | None = None
    summary: str | None = None


class ExtractedProfile(BaseModel):
    """What Gemini must return when asked to read a resume."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    website: str | None = None
    linkedin: str | None = None
    work_history: list[_ExtractedEntry] = []
    education: list[_ExtractedEntry] = []
    skills: list[str] = []
    background: str | None = None


def resume_text(path: Path) -> str:
    """Extract plain text from a resume PDF, or raise if it can't be read."""

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError) as error:
        raise ValueError(f"Could not read resume {path}: {error}") from error

    text = "\n".join(pages).strip()
    if not text:
        raise ValueError(f"No extractable text found in resume {path}")
    return text


def extract_profile_fields(text: str, client: LLMClient) -> dict[str, Any]:
    """Ask the LLM to read resume text into structured profile fields.

    Returns {} on any failure -- the caller falls back to manual entry.
    """

    prompt = (
        "Read this resume and extract structured profile fields as JSON. "
        "Only include information actually present in the text below; leave a "
        "field empty rather than guessing.\n\n"
        f"Resume:\n{text}"
    )
    try:
        extracted = client.complete_json(prompt, ExtractedProfile)
    except LLMError as error:
        print(f"LLM call failed: {error}", file=sys.stderr)
        return {}
    return extracted.model_dump(exclude_none=True)


def _slugify(*parts: str | None) -> str:
    text = "-".join(part for part in parts if part) or "entry"
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "entry"


def _entries_from_extracted(raw_entries: list[dict[str, Any]]) -> list[ProfileEntry]:
    entries: list[ProfileEntry] = []
    seen_ids: set[str] = set()
    for raw in raw_entries:
        base_id = _slugify(raw.get("organization"), raw.get("title"))
        entry_id = base_id
        suffix = 2
        while entry_id in seen_ids:
            entry_id = f"{base_id}-{suffix}"
            suffix += 1
        seen_ids.add(entry_id)
        entries.append(ProfileEntry(id=entry_id, **raw))
    return entries


def merge_profile(
    existing: Profile | None, extracted: dict[str, Any], answers: dict[str, Any]
) -> Profile:
    """Combine profile data with this precedence: answers > extracted > existing.

    `answers` are values the user explicitly typed during setup; a key present
    there always wins. Otherwise an extracted value fills a gap; otherwise the
    existing profile's value is kept. Nothing is silently dropped -- a field
    absent from all three sources is simply left unset.
    """

    merged: dict[str, Any] = existing.model_dump(mode="json") if existing else {}

    for key, value in extracted.items():
        if key in {"work_history", "education"}:
            merged[key] = [entry.model_dump(exclude_none=True) for entry in _entries_from_extracted(value)]
        elif value not in (None, "", []):
            merged[key] = value

    for key, value in answers.items():
        if value not in (None, ""):
            merged[key] = value

    return Profile.model_validate(merged)


def write_profile(profile: Profile, path: Path) -> None:
    """Write the profile as YAML, replacing any existing file atomically."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.replace(path.with_suffix(path.suffix + ".bak"))

    data = profile.model_dump(mode="json", exclude_none=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        file.write(_PROFILE_HEADER)
        yaml.safe_dump(data, file, sort_keys=False)
    tmp_path.replace(path)
