"""Deterministic validation and submission gating for form answers."""

from dataclasses import dataclass
from typing import Literal, Sequence

from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.generation import DraftAnswer, ValidatedAnswer
from jobapply.jev import ClaimSupport
from jobapply.mapping import (
    _is_known_question,
    _is_resume_question,
    _question_profile_value,
    normalize_label,
)
from jobapply.profile import Profile


@dataclass(frozen=True)
class Decision:
    """Whether all required form questions have validated answers."""

    action: Literal["submit", "defer"]
    reason_codes: list[str]
    validated_answer: ValidatedAnswer | None = None


def validate_draft(
    draft: DraftAnswer,
    evidence: Sequence,
    *,
    question: FormQuestion | None = None,
    jev_client=None,
    max_length: int | None = None,
) -> Decision:
    """Validate structure, citations, length, and Jev support as one boundary."""

    reasons: list[str] = []
    known_ids = {fact.id for fact in evidence}
    if not draft.claims:
        reasons.append("missing_factual_answer")
    for claim in draft.claims:
        if not claim.text.strip():
            reasons.append("blank_claim")
        if not claim.evidence_ids or any(
            evidence_id not in known_ids for evidence_id in claim.evidence_ids
        ):
            reasons.append("invalid_evidence_reference")
    answer = " ".join(claim.text.strip() for claim in draft.claims)
    limits = [limit for limit in (
        question.max_length if question is not None else None,
        max_length,
    ) if limit is not None]
    effective_max_length = min(limits) if limits else None
    if effective_max_length is not None and len(answer) > effective_max_length:
        reasons.append("answer_too_long")
    if reasons:
        return Decision(action="defer", reason_codes=reasons)

    if question is None or jev_client is None:
        return Decision(action="defer", reason_codes=["claim_support_unconfirmed"])

    jev_settings = getattr(jev_client, "settings", None)
    min_confidence = getattr(jev_settings, "jev_min_confidence", 0.98)
    evidence_by_id = {fact.id: fact for fact in evidence}
    support_failed = False
    for claim in draft.claims:
        cited = [evidence_by_id[identifier] for identifier in claim.evidence_ids]
        try:
            support = jev_client.check_claim_support(
                question.label,
                claim.text,
                [{"id": fact.id, "value": fact.value} for fact in cited],
            )
        except Exception:
            support_failed = True
            continue
        if not isinstance(support, ClaimSupport) or not claim_support_is_confirmed(
            support, claim.evidence_ids, min_confidence=min_confidence
        ):
            support_failed = True
    if support_failed:
        return Decision(action="defer", reason_codes=["claim_support_unconfirmed"])

    return Decision(
        action="submit",
        reason_codes=[],
        validated_answer=ValidatedAnswer(answer),
    )


def claim_support_is_confirmed(
    support: ClaimSupport, evidence_ids: Sequence[str], *, min_confidence: float = 0.98
) -> bool:
    """Return whether a support evaluation is sufficiently strong for review."""

    return (
        support.status == "supported"
        and support.confidence >= min_confidence
        and bool(evidence_ids)
        and support.evidence_ids == list(evidence_ids)
    )


def submission_decision(
    answers: Sequence[FieldAnswer],
    questions: Sequence[FormQuestion],
    profile: Profile,
) -> Decision:
    """Defer when a required question is unresolved or an answer is invalid."""

    answers_by_id = {answer.question_id: answer for answer in answers}
    reasons: list[str] = []

    def add_reason(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    for question in questions:
        if not question.required:
            continue
        if _is_resume_question(question):
            # Upload completion is an ATS operation, not a textual FieldAnswer.
            # Confirmed uploads must be removed from the unresolved questions.
            add_reason("missing_required_answer")
            continue
        if not _is_known_question(question) or question.kind not in {
            "text",
            "email",
            "phone",
            "select",
        }:
            add_reason("unknown_required_question")
            continue

        answer = answers_by_id.get(question.id)
        resolved = _question_profile_value(question, profile)
        sensitive = resolved is not None and resolved[2]
        if answer is None:
            if sensitive and resolved[0] is None:
                add_reason("sensitive_answer_missing")
            elif question.kind == "select" and resolved is not None and resolved[0] is not None:
                if not any(
                    normalize_label(option) == normalize_label(resolved[0])
                    for option in question.options
                ):
                    add_reason("invalid_option")
                else:
                    add_reason("missing_required_answer")
            else:
                add_reason("missing_required_answer")
            continue

        if resolved is None:
            # A caller-provided answer cannot stand in for an absent known
            # profile value. Generated drafts may be validated in a later task.
            add_reason("missing_required_answer")
            continue

        if sensitive:
            expected_evidence = resolved[1]
            expected_value = resolved[0]
            answer_value = answer.value
            if question.kind == "select" and expected_value is not None:
                expected_value = normalize_label(expected_value)
                answer_value = normalize_label(answer_value)
            if (
                expected_value is None
                or expected_evidence not in answer.evidence_ids
                or answer_value != expected_value
            ):
                add_reason("sensitive_answer_missing")
                continue
        elif resolved is not None and resolved[0] is not None:
            expected = (
                normalize_label(resolved[0])
                if question.kind == "select"
                else resolved[0]
            )
            actual = (
                normalize_label(answer.value)
                if question.kind == "select"
                else answer.value
            )
            if expected != actual or resolved[1] not in answer.evidence_ids:
                add_reason("missing_required_answer")
                continue

        if question.kind == "select" and not any(
            normalize_label(option) == normalize_label(answer.value)
            for option in question.options
        ):
            add_reason("invalid_option")

    return Decision(action="defer" if reasons else "submit", reason_codes=reasons)
