from pathlib import Path

from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.policy import submission_decision
from jobapply.profile import Profile


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


def test_unhandled_required_resume_upload_defers_without_a_textual_answer():
    question = FormQuestion("resume", "Resume Attachment", True, "unknown", [], None)

    decision = submission_decision([], [question], _profile())

    assert decision.action == "defer"
    assert decision.reason_codes == ["missing_required_answer"]
