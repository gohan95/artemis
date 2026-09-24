from pathlib import Path

import pytest

from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.policy import (
    claim_support_is_confirmed,
    submission_decision,
    validate_draft,
)
from jobapply.generation import DraftAnswer, SupportedClaim
from jobapply.jev import ClaimSupport
from jobapply.profile import EvidenceFact
from jobapply.profile import Profile
from jobapply.settings import Settings


def _profile(**kwargs):
    return Profile(resume_path=Path("resume.pdf"), **kwargs)


def test_required_unknown_question_defers_with_unknown_reason():
    question = FormQuestion("q1", "Anything else?", True, "unknown", [], None)

    decision = submission_decision([], [question], _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["unknown_required_question"]


def test_all_missing_required_fields_are_reported():
    questions = [
        FormQuestion("q1", "Email Address", True, "email", [], None),
        FormQuestion("q2", "Phone Number", True, "phone", [], None),
        FormQuestion("q3", "Email Address", False, "email", [], None),
    ]

    decision = submission_decision([], questions, _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["missing_required_answer"]


def test_missing_sensitive_answer_uses_sensitive_reason_code():
    question = FormQuestion(
        "q1", "Do you require sponsorship?", True, "select", ["Yes", "No"], None
    )

    decision = submission_decision([], [question], _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["sensitive_answer_missing"]


def test_select_answer_outside_available_options_defers():
    question = FormQuestion("q1", "Email Address", True, "select", ["other"], None)
    answer = FieldAnswer("q1", "ada@example.test", ["profile.email"], "profile")

    decision = submission_decision([answer], [question], _profile(email="ada@example.test"))

    assert decision.action == "defer"
    assert decision.reason_codes == ["invalid_option"]


def test_sensitive_answer_requires_matching_explicit_profile_evidence():
    question = FormQuestion(
        "q1", "Are you authorized to work in the United States?", True, "text", [], None
    )
    profile = _profile(sensitive_answers={"work_authorization": "authorized"})
    answer = FieldAnswer("q1", "authorized", ["resume.page.1"], "profile")

    decision = submission_decision([answer], [question], profile)

    assert decision.action == "defer"
    assert decision.reason_codes == ["sensitive_answer_missing"]


def test_explicit_sensitive_answer_with_its_evidence_can_submit():
    question = FormQuestion(
        "q1", "Are you authorized to work in the United States?", True, "text", [], None
    )
    profile = _profile(sensitive_answers={"work_authorization": "authorized"})
    answer = FieldAnswer(
        "q1",
        "authorized",
        ["profile.sensitive_answers.work_authorization"],
        "profile",
    )

    decision = submission_decision([answer], [question], profile)

    assert decision.action == "submit"
    assert decision.reason_codes == []


def test_answer_for_an_unrequired_question_does_not_block_submission():
    question = FormQuestion("q1", "Anything else?", False, "unknown", [], None)

    decision = submission_decision([], [question], _profile())

    assert decision.action == "submit"
    assert decision.reason_codes == []


def test_known_profile_answer_cannot_be_replaced_by_an_unbacked_value():
    question = FormQuestion("q1", "Email Address", True, "email", [], None)
    answer = FieldAnswer("q1", "other@example.test", [], "profile")

    decision = submission_decision(
        [answer], [question], _profile(email="ada@example.test")
    )

    assert decision.action == "defer"
    assert decision.reason_codes == ["missing_required_answer"]


def test_arbitrary_answer_cannot_fill_known_field_without_profile_value():
    question = FormQuestion("q1", "Email Address", True, "email", [], None)
    answer = FieldAnswer("q1", "arbitrary@example.test", ["profile.email"], "profile")

    decision = submission_decision([answer], [question], _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["missing_required_answer"]


def test_sensitive_select_option_case_normalization_preserves_explicit_evidence():
    question = FormQuestion(
        "q1",
        "Are you authorized to work in the United States?",
        True,
        "select",
        ["Yes"],
        None,
    )
    profile = _profile(sensitive_answers={"work_authorization": "yes"})
    answer = FieldAnswer(
        "q1",
        "Yes",
        ["profile.sensitive_answers.work_authorization"],
        "profile",
    )

    decision = submission_decision([answer], [question], profile)

    assert decision.action == "submit"
    assert decision.reason_codes == []


def test_unhandled_required_resume_upload_defers_without_a_textual_answer():
    question = FormQuestion("resume", "Resume Attachment", True, "unknown", [], None)

    decision = submission_decision([], [question], _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["missing_required_answer"]


def test_generated_draft_validation_only_checks_claim_evidence_and_length():
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led a team of eight.", evidence_ids=["work.acme.team"])]
    )
    evidence = [EvidenceFact(id="work.acme.team", value="Led eight engineers", source="profile")]

    class FakeJev:
        settings = Settings(jev_min_confidence=0.98)

        def check_claim_support(self, _question, claim, cited):
            return ClaimSupport("supported", 0.99, [fact["id"] for fact in cited])

    decision = validate_draft(
        draft,
        evidence,
        question=FormQuestion("q", "Describe leadership", True, "text", [], 80),
        jev_client=FakeJev(),
        max_length=80,
    )

    assert decision.validated_answer is not None
    assert decision.reason_codes == []


def test_structural_validation_alone_cannot_pass_without_jev_support():
    draft = DraftAnswer(
        claims=[SupportedClaim(text="I led eight engineers.", evidence_ids=["work.acme.team"])]
    )
    evidence = [EvidenceFact(id="work.acme.team", value="Led eight engineers", source="profile")]

    validation = validate_draft(draft, evidence, max_length=80)

    assert validation.action == "defer"


@pytest.mark.parametrize(
    "support",
    [
        ClaimSupport(status="unsupported", confidence=0.99, evidence_ids=["work.acme.team"]),
        ClaimSupport(status="supported", confidence=0.97, evidence_ids=["work.acme.team"]),
        ClaimSupport(status="supported", confidence=0.99, evidence_ids=["other.fact"]),
        ClaimSupport(status="defer", confidence=1.0, evidence_ids=["work.acme.team"]),
    ],
)
def test_claim_support_must_be_supported_cited_and_at_least_point_98(support):
    confirmed = claim_support_is_confirmed(support, ["work.acme.team"])

    assert confirmed is False


def test_draft_validation_checks_every_claim_using_only_its_references():
    class FakeJev:
        def __init__(self):
            self.calls = []

        def check_claim_support(self, question, claim, evidence):
            self.calls.append((question, claim, evidence))
            return ClaimSupport(
                status="supported",
                confidence=0.99,
                evidence_ids=[fact["id"] for fact in evidence],
            )

    evidence = [
        EvidenceFact(id="work.acme.team", value="Led eight engineers", source="profile"),
        EvidenceFact(id="work.acme.role", value="Engineering manager", source="profile"),
    ]
    draft = DraftAnswer(
        claims=[
            SupportedClaim(text="I led eight engineers.", evidence_ids=["work.acme.team"]),
            SupportedClaim(text="I was an engineering manager.", evidence_ids=["work.acme.role"]),
        ]
    )
    jev = FakeJev()

    result = validate_draft(
        draft,
        evidence,
        question=FormQuestion("q", "Describe leadership", True, "text", [], None),
        jev_client=jev,
        max_length=300,
    )
    assert result.validated_answer is not None
    assert jev.calls == [
        ("Describe leadership", "I led eight engineers.", [{"id": "work.acme.team", "value": "Led eight engineers"}]),
        ("Describe leadership", "I was an engineering manager.", [{"id": "work.acme.role", "value": "Engineering manager"}]),
    ]
