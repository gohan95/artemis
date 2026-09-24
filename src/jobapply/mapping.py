"""Deterministic exact-label mappings from form questions to profile facts."""

import re
from typing import Any

from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.profile import Profile


# Labels are deliberately explicit. Add aliases only when they have been
# observed and reviewed; normalization never broadens matching beyond equality.
_PROFILE_ALIASES = {
    "full name": ("full_name", "profile.full_name"),
    "name": ("full_name", "profile.full_name"),
    "email": ("email", "profile.email"),
    "email address": ("email", "profile.email"),
    "phone": ("phone", "profile.phone"),
    "phone number": ("phone", "profile.phone"),
    "mobile phone": ("phone", "profile.phone"),
    "location": ("location", "profile.location"),
    "current location": ("location", "profile.location"),
    "city, state": ("location", "profile.location"),
    "website": ("website", "profile.website"),
    "personal website": ("website", "profile.website"),
    "portfolio": ("website", "profile.website"),
    "linkedin": ("linkedin", "profile.linkedin"),
    "linkedin url": ("linkedin", "profile.linkedin"),
    "linkedin profile": ("linkedin", "profile.linkedin"),
}

_SENSITIVE_ALIASES = {
    "are you authorized to work in the united states?": "work_authorization",
    "are you legally authorized to work in the united states?": "work_authorization",
    "are you legally authorized to work in the country where this job is located?": "work_authorization",
    "will you now or in the future require sponsorship?": "sponsorship",
    "will you require sponsorship?": "sponsorship",
    "do you require sponsorship?": "sponsorship",
    "do you have a disability?": "disability",
    "do you identify as a person with a disability?": "disability",
    "are you a veteran?": "veteran_status",
    "do you identify as a veteran?": "veteran_status",
    "have you ever been convicted of a crime?": "criminal_history",
    "do you have a criminal history?": "criminal_history",
}

_SENSITIVE_QUESTION_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\bprotected class(?:es)?\b",
    r"\b(?:authorized|eligible|permitted) to work\b",
    r"\b(?:visa|immigration|sponsor(?:ship)?)\b",
    r"\b(?:disability|disabled|medical condition|accommodation)\b",
    r"\b(?:veteran|military|armed forces|military service)\b",
    r"\b(?:convict(?:ed|ion)?|criminal record|criminal history|felony|felonies)\b",
    r"\b(?:race|racial|ethnic(?:ity)?|national origin|religion|religious affiliation)\b",
    r"\b(?:gender identity|gender|sex|sexual orientation)\b",
    r"\b(?:date of birth|age|pregnan(?:t|cy)|marital status|genetic information)\b",
))

_RESUME_ALIASES = frozenset({"resume", "resume attachment", "attach resume"})
_SUPPORTED_KINDS = frozenset({"text", "email", "phone", "select"})


def normalize_label(value: str) -> str:
    """Case-fold and collapse whitespace without changing punctuation."""

    return " ".join(value.split()).casefold()


def is_sensitive_question(question: FormQuestion) -> bool:
    """Conservatively identify sensitive or protected-class application questions."""

    label = normalize_label(question.label)
    return label in _SENSITIVE_ALIASES or any(
        pattern.search(label) for pattern in _SENSITIVE_QUESTION_PATTERNS
    )


def _serialize(value: Any) -> str:
    """Render a structured profile value the same way load_evidence does."""

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


def _profile_value(question: FormQuestion, profile: Profile):
    """Return (value, evidence ID, sensitive) for an exact known label."""

    label = normalize_label(question.label)
    direct = _PROFILE_ALIASES.get(label)
    if direct is not None:
        value = getattr(profile, direct[0])
        if value is None or not _serialize(value):
            return None
        return _serialize(value), direct[1], False

    sensitive_key = _SENSITIVE_ALIASES.get(label)
    if sensitive_key is not None:
        value = profile.sensitive_answers.get(sensitive_key)
        if value is None or not _serialize(value):
            return None, f"profile.sensitive_answers.{sensitive_key}", True
        return (
            _serialize(value),
            f"profile.sensitive_answers.{sensitive_key}",
            True,
        )
    if label in _RESUME_ALIASES:
        return "resume", "profile.resume_path", False
    return None


def map_known_question(question: FormQuestion, profile: Profile) -> FieldAnswer | None:
    """Map only supported, exact aliases to a value backed by profile data."""

    if question.kind not in _SUPPORTED_KINDS:
        return None

    resolved = _profile_value(question, profile)
    if resolved is None:
        return None
    value, evidence_id, _sensitive = resolved
    if value is None:
        return None
    if evidence_id == "profile.resume_path":
        return None

    if question.kind == "select":
        matched_option = next(
            (
                option
                for option in question.options
                if normalize_label(option) == normalize_label(value)
            ),
            None,
        )
        if matched_option is None:
            return None
        value = matched_option

    return FieldAnswer(
        question_id=question.id,
        value=value,
        evidence_ids=[evidence_id],
        method="profile",
    )


def _question_profile_value(question: FormQuestion, profile: Profile):
    """Expose the resolved value for policy validation within this package."""

    return _profile_value(question, profile)


def _is_known_question(question: FormQuestion) -> bool:
    """Whether a question label has an explicit alias, including resume."""

    label = normalize_label(question.label)
    return (
        label in _PROFILE_ALIASES
        or label in _SENSITIVE_ALIASES
        or label in _RESUME_ALIASES
    )


def _is_resume_question(question: FormQuestion) -> bool:
    """Whether a question is a resume upload control rather than text input."""

    return normalize_label(question.label) in _RESUME_ALIASES
