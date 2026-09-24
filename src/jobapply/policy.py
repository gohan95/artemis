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


def validate_and_render_answer(
    draft: DraftAnswer,
    evidence: Sequence,
    *,
    question: FormQuestion,
    jev_client=None,
) -> tuple[Decision, str | None]:
    """Validate every cited claim and return renderable text only on success."""

    reasons: list[str] = []
    known_ids = {fact.id for fact in evidence}
    claims = getattr(draft, "claims", None)
    if not isinstance(claims, list) or not claims:
        reasons.append("missing_factual_answer")
        claims = []
    for claim in claims:
        text = getattr(claim, "text", None)
        evidence_ids = getattr(claim, "evidence_ids", None)
        if not isinstance(text, str) or not text.strip():
            reasons.append("blank_claim")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(
            not isinstance(evidence_id, str) or evidence_id not in known_ids
            for evidence_id in evidence_ids
        ):
            reasons.append("invalid_evidence_reference")
    answer = " ".join(
        claim.text.strip()
        for claim in claims
        if isinstance(getattr(claim, "text", None), str)
    )
    if question.max_length is not None and len(answer) > question.max_length:
        reasons.append("answer_too_long")
    if reasons:
        return Decision(action="defer", reason_codes=reasons), None

    if jev_client is None:
        return Decision(action="defer", reason_codes=["claim_support_unconfirmed"]), None

    jev_settings = getattr(jev_client, "settings", None)
    min_confidence = getattr(jev_settings, "jev_min_confidence", 0.98)
    evidence_by_id = {fact.id: fact for fact in evidence}
    support_failed = False
    for claim in claims:
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
        return Decision(action="defer", reason_codes=["claim_support_unconfirmed"]), None

    return Decision(action="submit", reason_codes=[]), answer


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
