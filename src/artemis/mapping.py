"""Label normalization and protected-question detection.

Aliases are deliberately explicit exact matches. Add one only after observing it on
a real posting; normalization never broadens matching beyond case/whitespace
equality, because a wrong guess here can put the wrong value into the wrong field.

`DECLARED_ALIASES` maps a protected question to the `profile.declared` key that may
answer it -- an explicit value the user wrote by hand, never inferred from another
field or another declared value (see `answers.py`).
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

DECLARED_ALIASES: dict[str, str] = {
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
    "what is your gender?": "gender",
    "what is your gender identity?": "gender",
    "gender identity": "gender",
    "what is your race/ethnicity?": "race_ethnicity",
    "what is your race?": "race_ethnicity",
    "race/ethnicity": "race_ethnicity",
    "what are your pronouns?": "pronouns",
    "pronouns": "pronouns",
    "what is your sexual orientation?": "sexual_orientation",
    "what is your date of birth?": "date_of_birth",
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


def is_protected_question(label: str) -> bool:
    """Conservatively identify protected-class/legally-significant application questions."""

    normalized = normalize_label(label)
    return normalized in DECLARED_ALIASES or any(
        pattern.search(normalized) for pattern in _SENSITIVE_QUESTION_PATTERNS
    )


# A closed taxonomy of recurring job-application question types, used to recognize
# the same question asked in different words (e.g. "Why do you want to work here?"
# vs "What interests you about this role?"). Deliberately closed, not open-vocabulary
# -- an open vocabulary produces near-duplicate types ("why_this_company",
# "company_interest") for the same question, and the whole point is collapsing
# rephrasings to one key. Plain strings, not an Enum: these persist to YAML and the
# user will hand-edit the learned-answers file.
#
# No protected question may ever map to one of these -- classification is a form of
# derivation, and protected questions resolve only from an explicit `declared` value
# (see `is_protected_question`, `answers.py`). Enforced by
# tests/test_mapping.py::test_no_canonical_phrase_is_a_protected_question.
CANONICAL_TYPES = frozenset({
    "why_company",
    "why_role",
    "why_leaving",
    "salary_expectation",
    "start_date",
    "notice_period",
    "relocation_willingness",
    "remote_preference",
    "work_location_onsite_days",
    "referral_source",
    "how_heard",
    "portfolio_link",
    "github_link",
    "years_experience",
    "cover_letter",
    "additional_info",
    "greatest_strength",
    "challenging_project",
    "team_fit",
})

# Seed phrasings per canonical type, normalized-form keyed. Add a phrasing only
# after observing it on a real posting -- same discipline as PROFILE_ALIASES.
CANONICAL_PHRASES: dict[str, str] = {
    "why do you want to work here": "why_company",
    "why do you want to work at this company": "why_company",
    "what interests you about our company": "why_company",
    "what attracts you to this company": "why_company",
    "why are you interested in this role": "why_role",
    "why are you interested in this position": "why_role",
    "what interests you about this role": "why_role",
    "why do you want this job": "why_role",
    "why are you leaving your current job": "why_leaving",
    "why are you looking to leave your current company": "why_leaving",
    "reason for leaving": "why_leaving",
    "what are your salary expectations": "salary_expectation",
    "desired salary": "salary_expectation",
    "expected compensation": "salary_expectation",
    "desired start date": "start_date",
    "when can you start": "start_date",
    "earliest start date": "start_date",
    "what is your notice period": "notice_period",
    "notice period": "notice_period",
    "are you willing to relocate": "relocation_willingness",
    "willing to relocate": "relocation_willingness",
    "what is your remote work preference": "remote_preference",
    "do you prefer remote, hybrid, or onsite work": "remote_preference",
    "how many days a week can you work from our office": "work_location_onsite_days",
    "how did you hear about this position": "how_heard",
    "how did you hear about us": "how_heard",
    "how did you find out about this job": "how_heard",
    "who referred you": "referral_source",
    "referral name": "referral_source",
    "portfolio url": "portfolio_link",
    "portfolio link": "portfolio_link",
    "github url": "github_link",
    "github profile": "github_link",
    "years of experience": "years_experience",
    "how many years of experience do you have": "years_experience",
    "cover letter": "cover_letter",
    "anything else you would like us to know": "additional_info",
    "additional information": "additional_info",
    "what is your greatest strength": "greatest_strength",
    "describe a challenging project you worked on": "challenging_project",
    "how do you work on a team": "team_fit",
}


def canonical_form(label: str) -> str:
    """Normalize harder than `normalize_label`, for canonical-type matching only.

    Strips trailing punctuation, leading enumerators ("1. ", "*"), and a trailing
    "(optional)"/"(required)" marker on top of normalize_label's casefold and
    whitespace-collapse. Kept separate from `normalize_label` deliberately: the
    alias tables (PROFILE_ALIASES, DECLARED_ALIASES) depend on normalize_label's
    exact current behavior, and widening it would silently change what matches
    the protected-question table.
    """

    text = normalize_label(label)
    text = re.sub(r"^(?:[*\-•]|\d+[.)])\s*", "", text)
    text = re.sub(r"\s*\((?:optional|required)\)\s*$", "", text)
    text = text.rstrip("?!.: ")
    return text.strip()


def canonical_type(label: str) -> str | None:
    """Tier A: deterministic lookup only. Returns None on a miss -- the caller
    may fall back to model classification or leave the question unresolved.
    Never called for a protected question; see the module-level note above.
    """

    return CANONICAL_PHRASES.get(canonical_form(label))
