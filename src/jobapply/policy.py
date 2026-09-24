"""Deterministic validation and submission gating for form answers."""

from dataclasses import dataclass
from typing import Literal, Sequence

from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.generation import DraftAnswer
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


def validate_draft(
    draft: DraftAnswer, evidence: Sequence, *, max_length: int | None
) -> Decision:
    """Check generated claim structure, citations, factual presence, and length."""

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
    if max_length is not None and len(answer) > max_length:
        reasons.append("answer_too_long")
    return Decision(action="defer" if reasons else "submit", reason_codes=reasons)


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


def evaluate_draft_support(
    draft: DraftAnswer,
    evidence: Sequence,
    question: FormQuestion,
    jev_client,
    *,
    min_confidence: float = 0.98,
) -> bool:
    """Evaluate each claim against only its cited facts; this is not submit auth."""

    evidence_by_id = {fact.id: fact for fact in evidence}
    for claim in draft.claims:
        if not claim.evidence_ids or any(
            identifier not in evidence_by_id for identifier in claim.evidence_ids
        ):
            return False
        cited = [evidence_by_id[identifier] for identifier in claim.evidence_ids]
        support = jev_client.check_claim_support(
            question.label,
            claim.text,
            [{"id": fact.id, "value": fact.value} for fact in cited],
        )
        if not claim_support_is_confirmed(
            support, claim.evidence_ids, min_confidence=min_confidence
        ):
            return False
    return bool(draft.claims)


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
