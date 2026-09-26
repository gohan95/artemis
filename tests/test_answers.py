"""Tests for question resolution: aliasing, protected-question fail-closed behavior, learning."""

from pathlib import Path

import pytest

from artemis.answers import _preference_value, resolve_question
from artemis.answers_store import LearnedAnswers
from artemis.forms import FormQuestion
from artemis.mapping import PREFERENCE_ALIASES, is_protected_question
from artemis.profile import Preferences, Profile


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
def test_is_protected_question_detects_known_wording(label: str):
    assert is_protected_question(label) is True


def test_is_protected_question_false_for_ordinary_question():
    assert is_protected_question("What is your email address?") is False


def test_protected_question_without_declared_value_is_never_answered(learned):
    profile = make_profile(declared={})
    question = FormQuestion(
        id="work-auth", label="Are you authorized to work in the United States?",
        required=True, kind="select", options=["Yes", "No"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.undeclared is True


def test_protected_question_with_declared_value_is_answered(learned):
    profile = make_profile(declared={"work_authorization": "Yes"})
    question = FormQuestion(
        id="work-auth", label="Are you authorized to work in the United States?",
        required=True, kind="select", options=["Yes", "No"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is not None
    assert resolution.answer.value == "Yes"
    assert resolution.answer.method == "profile"


def test_protected_question_never_reads_learned_answers(learned):
    learned.record("Are you a veteran?", "No")
    profile = make_profile(declared={})
    question = FormQuestion(id="vet", label="Are you a veteran?", required=True, kind="select", options=["Yes", "No"])
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.undeclared is True


def test_declared_value_cannot_answer_a_different_category(learned):
    """A veteran-status value must never answer a disability question -- no
    cross-category derivation, even when a select's options happen to match."""

    profile = make_profile(declared={"veteran_status": "No"})
    question = FormQuestion(
        id="disability", label="Do you have a disability?",
        required=True, kind="select", options=["Yes", "No"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None
    assert resolution.undeclared is True


def test_declared_demographic_value_is_answered(learned):
    profile = make_profile(declared={"gender": "Prefer not to say"})
    question = FormQuestion(
        id="gender", label="What is your gender identity?",
        required=False, kind="select", options=["Male", "Female", "Prefer not to say"],
    )
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is not None
    assert resolution.answer.value == "Prefer not to say"
    assert resolution.answer.method == "profile"


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
    assert resolution.undeclared is False


def test_unknown_label_is_unresolved(learned):
    profile = make_profile()
    question = FormQuestion(id="q1", label="Why do you want to work here?", required=True, kind="text")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer is None


def test_learned_answer_resolves_on_subsequent_ask(learned):
    profile = make_profile()
    question = FormQuestion(id="q1", label="Desired start date", required=False, kind="text")
    assert resolve_question(question, profile, learned).answer is None

    learned.record("Desired start date", "Immediately")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer.value == "Immediately"
    assert resolution.answer.method == "learned"


def test_resume_alias_resolves_to_resume_path(learned):
    profile = make_profile()
    question = FormQuestion(id="resume", label="Resume", required=True, kind="file")
    resolution = resolve_question(question, profile, learned)
    assert resolution.answer.value == str(profile.resume_path)


def test_canonical_type_match_survives_rephrasing(learned):
    """The regression this store rewrite exists to fix: two phrasings of the
    same recognized question type ("why_company") now share one learned
    answer, including across a trailing '?' that normalize_label alone would
    treat as a different cache key."""

    profile = make_profile()
    learned.record("Why do you want to work here?", "Because the mission resonates with me.")

    with_mark = FormQuestion(id="q1", label="Why do you want to work here?", required=False, kind="text")
    rephrased = FormQuestion(id="q2", label="What interests you about our company", required=False, kind="text")

    assert resolve_question(with_mark, profile, learned).answer.value == "Because the mission resonates with me."
    assert resolve_question(rephrased, profile, learned).answer.value == "Because the mission resonates with me."


def test_no_preference_alias_is_a_protected_question():
    """Structural guard, matching the canonical-type one in test_mapping.py:
    a preference and a protected question must never overlap. A protected
    question must never be answerable merely by having typed a preference."""

    for label in PREFERENCE_ALIASES:
        assert is_protected_question(label) is False, f"{label!r} is flagged protected"


def test_preference_resolves_a_scalar_string_field(learned):
    profile = make_profile(preferences=Preferences(salary_expectation="150-170k"))
    question = FormQuestion(id="q1", label="What are your salary expectations?", required=False, kind="text")

    resolution = resolve_question(question, profile, learned)

    assert resolution.answer.value == "150-170k"
    assert resolution.answer.method == "profile"


def test_unset_preference_falls_through_to_a_live_prompt_not_a_defer(learned):
    profile = make_profile()
    question = FormQuestion(id="q1", label="Desired start date", required=False, kind="text")

    resolution = resolve_question(question, profile, learned)

    assert resolution.answer is None
    assert resolution.undeclared is False


def test_bool_preference_matches_a_yes_no_select_option(learned):
    profile = make_profile(preferences=Preferences(willing_to_relocate=True))
    question = FormQuestion(
        id="q1", label="Are you willing to relocate?", required=False, kind="select", options=["Yes", "No"],
    )

    resolution = resolve_question(question, profile, learned)

    assert resolution.answer.value == "Yes"


def test_bool_preference_with_no_matching_select_option_is_unresolved(learned):
    profile = make_profile(preferences=Preferences(willing_to_relocate=True))
    question = FormQuestion(
        id="q1", label="Are you willing to relocate?", required=False,
        kind="select", options=["Definitely", "Not at this time"],
    )

    resolution = resolve_question(question, profile, learned)

    assert resolution.answer is None


def test_bool_preference_resolves_as_yes_no_text_for_a_free_text_question(learned):
    profile = make_profile(preferences=Preferences(willing_to_relocate=False))
    question = FormQuestion(id="q1", label="Willing to relocate", required=False, kind="text")

    resolution = resolve_question(question, profile, learned)

    assert resolution.answer.value == "No"


def test_list_preference_joins_for_a_text_field():
    """locations/employment_types have no alias yet (see mapping.py's
    PREFERENCE_ALIASES) so this exercises the formatting helper directly
    rather than through resolve_question."""

    profile = make_profile(preferences=Preferences(locations=["Remote", "Portland, OR"]))
    question = FormQuestion(id="q1", label="Preferred locations", required=False, kind="text")

    assert _preference_value(question, profile.preferences, "locations") == "Remote, Portland, OR"


def test_list_preference_is_unresolved_for_a_select():
    profile = make_profile(preferences=Preferences(employment_types=["Full-time"]))
    question = FormQuestion(id="q1", label="Employment Type", required=False, kind="select", options=["Full-time"])

    assert _preference_value(question, profile.preferences, "employment_types") is None
