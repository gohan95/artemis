"""Tests for question resolution: aliasing, sensitive fail-closed behavior, learning."""

from pathlib import Path

import pytest

from artemis.answers import resolve_question
from artemis.answers_store import LearnedAnswers
from artemis.forms import FormQuestion
from artemis.mapping import is_sensitive_question
from artemis.profile import Profile


def make_profile(**overrides) -> Profile:
    defaults = dict(resume_path=Path(__file__), full_name="Riley Example", email="riley@example.test")
    defaults.update(overrides)
    return Profile(**defaults)


@pytest.fixture
def learned(tmp_path: Path) -> LearnedAnswers:
    return LearnedAnswers(tmp_path / "learned.yaml")


@pytest.mark.parametrize(
    "label",
    [
        "Are you authorized to work in the United States?",
        "Will you require sponsorship?",
        "Do you have a disability?",
        "Are you a veteran?",
        "Have you ever been convicted of a crime?",
        "What is your gender identity?",
        "Do you require visa sponsorship now or in the future?",
    ],
)
def test_is_sensitive_question_detects_known_wording(label: str):
    assert is_sensitive_question(label) is True


def test_is_sensitive_question_false_for_ordinary_question():
    assert is_sensitive_question("What is your email address?") is False


def test_sensitive_question_without_explicit_value_is_never_answered(learned):
    profile = make_profile(sensitive_answers={})
    question = FormQuestion(
        id="work-auth", label="Are you authorized to work in the United States?",
        required=True, kind="select", options=["Yes", "No"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.sensitive_unanswered is True


def test_sensitive_question_with_explicit_value_is_answered(learned):
    profile = make_profile(sensitive_answers={"work_authorization": "Yes"})
    question = FormQuestion(
        id="work-auth", label="Are you authorized to work in the United States?",
        required=True, kind="select", options=["Yes", "No"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is not None
    assert resolution.answer.value == "Yes"
    assert resolution.answer.method == "profile"


def test_sensitive_question_never_reads_learned_answers(learned):
    learned.set("Are you a veteran?", "No")
    profile = make_profile(sensitive_answers={})
    question = FormQuestion(id="vet", label="Are you a veteran?", required=True, kind="select", options=["Yes", "No"])
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.sensitive_unanswered is True


def test_alias_maps_known_contact_field(learned):
    profile = make_profile(email="riley@example.test")
    question = FormQuestion(id="q1", label="Email Address", required=True, kind="email")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer.value == "riley@example.test"
    assert resolution.answer.method == "profile"


def test_alias_field_with_no_profile_value_is_unresolved(learned):
    profile = make_profile(website=None)
    question = FormQuestion(id="q1", label="Website", required=False, kind="text")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.sensitive_unanswered is False


def test_unknown_label_is_unresolved(learned):
    profile = make_profile()
    question = FormQuestion(id="q1", label="Why do you want to work here?", required=True, kind="text")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None


def test_learned_answer_resolves_on_subsequent_ask(learned):
    profile = make_profile()
    question = FormQuestion(id="q1", label="Desired start date", required=False, kind="text")
    assert resolve_question(question, profile, learned).answer is None

    learned.set("Desired start date", "Immediately")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer.value == "Immediately"
    assert resolution.answer.method == "learned"


def test_resume_alias_resolves_to_resume_path(learned):
    profile = make_profile()
    question = FormQuestion(id="resume", label="Resume", required=True, kind="file")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer.value == str(profile.resume_path)
