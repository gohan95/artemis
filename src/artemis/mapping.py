"""Label normalization and sensitive-question detection.

Aliases are deliberately explicit exact matches. Add one only after observing it on
a real posting; normalization never broadens matching beyond case/whitespace
equality, because a wrong guess here can put the wrong value into the wrong field.
"""

import re

PROFILE_ALIASES: dict[str, str] = {
    "full name": "full_name",
    "name": "full_name",
    "email": "email",
    "email address": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile phone": "phone",
    "location": "location",
    "current location": "location",
    "city, state": "location",
    "website": "website",
    "personal website": "website",
    "portfolio": "website",
    "linkedin": "linkedin",
    "linkedin url": "linkedin",
    "linkedin profile": "linkedin",
}

SENSITIVE_ALIASES: dict[str, str] = {
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

_SENSITIVE_QUESTION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bprotected class(?:es)?\b",
        r"\b(?:authorized|eligible|permitted) to work\b",
        r"\b(?:visa|immigration|sponsor(?:ship)?)\b",
        r"\b(?:disability|disabled|medical condition|accommodation)\b",
        r"\b(?:veteran|military|armed forces|military service)\b",
        r"\b(?:arrest(?:ed|s)?|charg(?:e|ed|es|ing)|offen[cs]e(?:s)?|"
        r"incarcerat(?:ed|ion)|plea(?:s)?|convict(?:ed|ion(?:s)?)?|"
        r"expung(?:e|ed|ement(?:s)?)|criminal record|criminal history|felon(?:y|ies))\b",
        r"\b(?:race|racial|ethnic(?:ity)?|national origin|religion|religious affiliation)\b",
        r"\b(?:gender identity|gender|sex|sexual orientation)\b",
        r"\b(?:date of birth|age|pregnan(?:t|cy)|marital status|genetic information)\b",
    )
)

RESUME_ALIASES = frozenset({"resume", "resume attachment", "attach resume"})


def normalize_label(value: str) -> str:
    """Case-fold and collapse whitespace without changing punctuation."""

    return " ".join(value.split()).casefold()


def is_sensitive_question(label: str) -> bool:
    """Conservatively identify sensitive or protected-class application questions."""

    normalized = normalize_label(label)
    return normalized in SENSITIVE_ALIASES or any(
        pattern.search(normalized) for pattern in _SENSITIVE_QUESTION_PATTERNS
    )
