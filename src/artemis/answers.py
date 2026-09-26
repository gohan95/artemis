"""Resolve one form question to a value, in a fixed, fail-closed order.

Resolution order for a question label:
  1. exact profile alias (name, email, phone, location, website, linkedin)
  2. exact preference alias (salary, start date, relocation, ...) -> profile.preferences
  3. protected question -> only an explicit `profile.declared` value, never derived
  4. previously learned answer (from a prior live prompt)
  5. otherwise unresolved -- the caller must ask the user

A protected question (work authorization, sponsorship, disability, veteran status,
criminal history, demographics, ...) is answered only from an explicit `declared`
value the user wrote by hand -- it is never inferred from another field, never
matched across categories, never read from the learned-answers store, and never
put to the user as a live prompt while an application is in progress. Declared
values otherwise behave like any other profile field: once written, they fill and
submit automatically, with no per-application review.

A preference (rung 2) is also an explicit value the user typed once, during
`artemis setup` -- but unlike a declared value, an unset preference is not a
reason to defer. It falls through to a normal live prompt, exactly like a
contact field the profile happens not to have (see rung 1's comment below).
"""

from dataclasses import dataclass

from artemis.answers_store import LearnedAnswers
from artemis.forms import FieldAnswer, FormQuestion
from artemis.mapping import (
    PREFERENCE_ALIASES,
    PROFILE_ALIASES,
    RESUME_ALIASES,
    is_protected_question,
    normalize_label,
)
from artemis.profile import Preferences, Profile


@dataclass(frozen=True)
class Resolution:
    """The outcome of trying to resolve one question, for the caller to act on."""

    answer: FieldAnswer | None
    # Set only when a protected question has no explicit declared value: the
    # pipeline must defer, not prompt the user or fall back to a guess.
    undeclared: bool = False


def resolve_question(
    question: FormQuestion, profile: Profile, learned: LearnedAnswers
) -> Resolution:
    """Resolve a question against the profile and learned answers, fail-closed."""

    label = normalize_label(question.label)

    if label in RESUME_ALIASES:
        return Resolution(FieldAnswer(question.id, str(profile.resume_path), "profile"))

    field_name = PROFILE_ALIASES.get(label)
    if field_name is not None:
        value = getattr(profile, field_name, None)
        if value:
            return Resolution(_matched_answer(question, str(value), "profile"))
        # A known contact field with no profile value is still unresolved, not
        # protected -- fall through so the caller can prompt for it normally.

    preference_field = PREFERENCE_ALIASES.get(label)
    if preference_field is not None:
        value = _preference_value(question, profile.preferences, preference_field)
        if value is not None:
            return Resolution(_matched_answer(question, value, "profile"))
        # Same as an unset contact field above: falls through to a live
        # prompt, not a defer -- a preference is never a reason to block.

    if is_protected_question(question.label):
        declared_key = _declared_key_for(label)
        value = profile.declared.get(declared_key) if declared_key else None
        if value is None:
            return Resolution(answer=None, undeclared=True)
        return Resolution(_matched_answer(question, str(value), "profile"))

    learned_value = learned.get(question.label, question.kind, question.options)
    if learned_value is not None:
        return Resolution(_matched_answer(question, learned_value, "learned"))

    return Resolution(answer=None)


def _declared_key_for(normalized_label: str) -> str | None:
    from artemis.mapping import DECLARED_ALIASES

    return DECLARED_ALIASES.get(normalized_label)


def _preference_value(question: FormQuestion, preferences: Preferences, field: str) -> str | None:
    """The preference's value formatted for this question's control, or None
    if it's unset or doesn't fit the control (e.g. a bool with no matching
    yes/no-ish option) -- either way, a caller must treat None the same as
    "no preference recorded", not force a value in.
    """

    value = getattr(preferences, field, None)

    if isinstance(value, bool):
        if question.kind != "select" or not question.options:
            return "Yes" if value else "No"
        wanted = normalize_label("yes" if value else "no")
        return next(
            (option for option in question.options if normalize_label(option) == wanted), None
        )

    if isinstance(value, list):
        if not value:
            return None
        if question.kind == "select":
            return None
        return ", ".join(str(item) for item in value)

    if value in (None, ""):
        return None
    return str(value)


def _matched_answer(question: FormQuestion, value: str, method: str) -> FieldAnswer:
    """Match a resolved value against select options by label, else use it as-is."""

    if question.kind == "select" and question.options:
        matched = next(
            (option for option in question.options if normalize_label(option) == normalize_label(value)),
            None,
        )
        if matched is not None:
            value = matched
    return FieldAnswer(question.id, value, method)  # type: ignore[arg-type]
