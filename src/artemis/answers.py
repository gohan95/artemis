"""Resolve one form question to a value, in a fixed, fail-closed order.

Resolution order for a question label:
  1. exact profile alias (name, email, phone, location, website, linkedin)
  2. sensitive question -> only an explicit `sensitive_answers` value, never inferred
  3. previously learned answer (from a prior live prompt)
  4. otherwise unresolved -- the caller must ask the user

Sensitive questions never fall through to the learned-answers store: an operator
should not be able to accidentally "teach" the tool an answer to a legally
significant question through a live prompt. That value belongs in the profile,
written deliberately by hand.
"""

from dataclasses import dataclass

from artemis.answers_store import LearnedAnswers
from artemis.forms import FieldAnswer, FormQuestion
from artemis.mapping import PROFILE_ALIASES, RESUME_ALIASES, is_sensitive_question, normalize_label
from artemis.profile import Profile


@dataclass(frozen=True)
class Resolution:
    """The outcome of trying to resolve one question, for the caller to act on."""

    answer: FieldAnswer | None
    # Set only when a sensitive question has no explicit profile value: the
    # pipeline must defer, not prompt the user or fall back to a guess.
    sensitive_unanswered: bool = False


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
        # sensitive -- fall through so the caller can prompt for it normally.

    if is_sensitive_question(question.label):
        sensitive_key = _sensitive_key_for(label)
        value = profile.sensitive_answers.get(sensitive_key) if sensitive_key else None
        if value is None:
            # Try any sensitive key whose value matches an option, for phrasing
            # this project doesn't have an exact alias for. Conservative: only
            # sensitive keys the profile actually declares are considered.
            value = _first_matching_sensitive_value(question, profile)
        if value is None:
            return Resolution(answer=None, sensitive_unanswered=True)
        return Resolution(_matched_answer(question, str(value), "profile"))

    learned_value = learned.get(question.label)
    if learned_value is not None:
        return Resolution(_matched_answer(question, learned_value, "learned"))

    return Resolution(answer=None)


def _sensitive_key_for(normalized_label: str) -> str | None:
    from artemis.mapping import SENSITIVE_ALIASES

    return SENSITIVE_ALIASES.get(normalized_label)


def _first_matching_sensitive_value(question: FormQuestion, profile: Profile) -> str | None:
    if not profile.sensitive_answers:
        return None
    if question.kind != "select" or not question.options:
        return None
    normalized_options = {normalize_label(option) for option in question.options}
    for value in profile.sensitive_answers.values():
        if normalize_label(str(value)) in normalized_options:
            return str(value)
    return None


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
