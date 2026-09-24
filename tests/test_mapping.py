from pathlib import Path

import pytest

from jobapply.forms import FormQuestion
from jobapply.mapping import map_known_question
from jobapply.profile import Profile


@pytest.fixture
def profile():
    return Profile(
        resume_path=Path("resume.pdf"),
        full_name="Ada Example",
        email="ada@example.test",
        phone="555-0100",
        location="Oakland, CA",
        website="https://ada.example.test",
        linkedin="https://linkedin.example.test/in/ada",
        sensitive_answers={"work_authorization": "authorized"},
    )


@pytest.mark.parametrize(
    ("label", "kind", "value", "evidence_id"),
    [
        ("Full Name", "text", "Ada Example", "profile.full_name"),
        ("Email Address", "email", "ada@example.test", "profile.email"),
        ("Phone Number", "phone", "555-0100", "profile.phone"),
        ("Location", "text", "Oakland, CA", "profile.location"),
        ("Website", "text", "https://ada.example.test", "profile.website"),
        (
            "LinkedIn URL",
            "text",
            "https://linkedin.example.test/in/ada",
            "profile.linkedin",
        ),
        (
            "Are you authorized to work in the United States?",
            "select",
            "authorized",
            "profile.sensitive_answers.work_authorization",
        ),
    ],
)
def test_exact_alias_maps_to_profile_value_and_evidence(
    profile, label, kind, value, evidence_id
):
    options = ["authorized"] if kind == "select" else []
    question = FormQuestion("q1", label, True, kind, options, None)

    answer = map_known_question(question, profile)

    assert answer is not None
    assert answer.question_id == "q1"
    assert answer.value == value
    assert answer.evidence_ids == [evidence_id]
    assert answer.method == "profile"


def test_exact_select_value_matches_option_after_normalization(profile):
    question = FormQuestion(
        "q1", "Email Address", True, "select", ["  ADA@EXAMPLE.TEST  "], None
    )

    answer = map_known_question(question, profile)

    assert answer is not None
    assert answer.value == "  ADA@EXAMPLE.TEST  "


def test_sensitive_select_mapping_preserves_option_case_and_evidence(profile):
    profile.sensitive_answers["work_authorization"] = "yes"
    question = FormQuestion(
        "q1",
        "Are you authorized to work in the United States?",
        True,
        "select",
        ["Yes"],
        None,
    )

    answer = map_known_question(question, profile)

    assert answer is not None
    assert answer.value == "Yes"
    assert answer.evidence_ids == ["profile.sensitive_answers.work_authorization"]


@pytest.mark.parametrize(
    "label", ["Your email", "Please provide your email address", "Email Address (required)"]
)
def test_mapping_does_not_use_fuzzy_or_substring_label_matches(profile, label):
    question = FormQuestion("q1", label, True, "email", [], None)

    assert map_known_question(question, profile) is None


@pytest.mark.parametrize("kind", ["checkbox", "unknown"])
def test_unsupported_question_kind_is_not_mapped(profile, kind):
    question = FormQuestion("q1", "Email Address", True, kind, [], None)

    assert map_known_question(question, profile) is None


def test_select_with_no_exact_normalized_option_match_is_not_mapped(profile):
    question = FormQuestion("q1", "Email Address", True, "select", ["other"], None)

    assert map_known_question(question, profile) is None


def test_sensitive_alias_without_explicit_profile_value_is_not_mapped(profile):
    question = FormQuestion(
        "q1", "Do you require sponsorship?", True, "select", ["Yes", "No"], None
    )

    assert map_known_question(question, profile) is None


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Will you require sponsorship?", "sponsorship"),
        ("Do you have a disability?", "disability"),
        ("Are you a veteran?", "veteran_status"),
        ("Do you have a criminal history?", "criminal_history"),
    ],
)
def test_each_sensitive_category_requires_its_own_explicit_value(profile, label, key):
    question = FormQuestion("q1", label, True, "text", [], None)

    assert map_known_question(question, profile) is None

    profile.sensitive_answers[key] = "explicit value"
    answer = map_known_question(question, profile)

    assert answer is not None
    assert answer.value == "explicit value"
    assert answer.evidence_ids == [f"profile.sensitive_answers.{key}"]


def test_resume_attachment_is_not_a_textual_field_answer(profile):
    question = FormQuestion("resume", "Resume", True, "text", [], None)

    assert map_known_question(question, profile) is None
