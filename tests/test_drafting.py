"""Tests for evidence-grounded answer drafting: grounding corpus, validation,
and graceful degradation when the LLM fails or returns nothing usable."""

from pathlib import Path

import pytest

from artemis.ats.base import JobContext
from artemis.drafting import DraftResponse, GroundedDrafter, build_prompt, profile_facts, validate_draft
from artemis.forms import FormQuestion
from artemis.llm import LLMError
from artemis.profile import Profile, ProfileEntry


def make_profile(**overrides) -> Profile:
    defaults = dict(resume_path=Path(__file__))
    defaults.update(overrides)
    return Profile(**defaults)


def test_profile_facts_includes_work_history_education_skills_background_goals():
    profile = make_profile(
        work_history=[
            ProfileEntry(id="acme-eng", title="Engineer", organization="Acme", summary="Built widgets."),
        ],
        education=[ProfileEntry(id="mit-cs", title="BS CS", organization="MIT")],
        skills=["Python", "SQL"],
        background="Senior engineer with 8 years in distributed systems.",
        goals="Looking for backend roles.",
    )

    facts = profile_facts(profile)

    assert facts["acme-eng"] == "Engineer -- Acme -- Built widgets."
    assert facts["mit-cs"] == "BS CS -- MIT"
    assert facts["skills"] == "Python, SQL"
    assert facts["background"] == "Senior engineer with 8 years in distributed systems."
    assert facts["goals"] == "Looking for backend roles."


def test_profile_facts_empty_for_bare_profile():
    assert profile_facts(make_profile()) == {}


def test_validate_draft_accepts_well_cited_answer():
    facts = {"acme-eng": "Engineer -- Acme"}
    response = DraftResponse(answer="I built things at Acme.", evidence_ids=["acme-eng"])

    draft = validate_draft(response, facts)

    assert draft is not None
    assert draft.text == "I built things at Acme."
    assert draft.evidence_ids == ("acme-eng",)


def test_validate_draft_rejects_fabricated_evidence_id():
    facts = {"acme-eng": "Engineer -- Acme"}
    response = DraftResponse(answer="I worked at a place not in your profile.", evidence_ids=["ghost-job"])

    assert validate_draft(response, facts) is None


def test_validate_draft_rejects_empty_answer():
    facts = {"acme-eng": "Engineer -- Acme"}
    response = DraftResponse(answer="   ", evidence_ids=["acme-eng"])

    assert validate_draft(response, facts) is None


def test_validate_draft_rejects_no_evidence_cited():
    facts = {"acme-eng": "Engineer -- Acme"}
    response = DraftResponse(answer="A generic answer with nothing grounding it.", evidence_ids=[])

    assert validate_draft(response, facts) is None


def test_validate_draft_rejects_a_draft_citing_only_a_style_example():
    """The core safety property of stage 4: style examples must never be
    addable to the citable-facts corpus. If a future change adds prior
    answers to `profile_facts` (or otherwise lets `evidence_ids` reference a
    style example), a draft could satisfy validate_draft by citing nothing
    but the model's own earlier output -- a hallucination that survived one
    review becoming permanent "evidence" for every future answer. facts here
    intentionally excludes the style-example id to prove that path stays
    closed regardless of what build_prompt shows the model."""

    facts = {"acme-eng": "Engineer -- Acme"}
    response = DraftResponse(answer="Echoing my past answer verbatim.", evidence_ids=["style-example-1"])

    assert validate_draft(response, facts) is None


def test_build_prompt_puts_style_examples_in_a_non_citable_section():
    prompt = build_prompt(
        FormQuestion(id="q1", label="Why do you want to work here?", required=False, kind="text"),
        facts={"acme-eng": "Engineer -- Acme"},
        style_examples=("I love solving hard distributed-systems problems.",),
    )

    assert "I love solving hard distributed-systems problems." in prompt
    assert "do NOT treat these as facts you may cite" in prompt or "NOT facts you may cite" in prompt


def test_build_prompt_omits_style_section_when_no_examples():
    prompt = build_prompt(
        FormQuestion(id="q1", label="Why do you want to work here?", required=False, kind="text"),
        facts={"acme-eng": "Engineer -- Acme"},
    )

    assert "previous answers" not in prompt.lower()


def test_build_prompt_includes_job_context_as_background_only():
    prompt = build_prompt(
        FormQuestion(id="q1", label="Why do you want to work here?", required=False, kind="text"),
        facts={"acme-eng": "Engineer -- Acme"},
        job=JobContext(company="Acme", role="Staff Engineer", requirements="Distributed systems experience."),
    )

    assert "Acme" in prompt
    assert "Staff Engineer" in prompt
    assert "do not cite" in prompt.lower()


def test_build_prompt_keeps_no_company_information_sentence_when_job_context_empty():
    prompt = build_prompt(
        FormQuestion(id="q1", label="Why do you want to work here?", required=False, kind="text"),
        facts={"acme-eng": "Engineer -- Acme"},
        job=JobContext(),
    )

    assert "You have not been given any" in prompt


def test_validate_draft_rejects_near_verbatim_replay_of_a_style_example():
    facts = {"acme-eng": "Engineer -- Acme"}
    example = "I am drawn to solving hard distributed-systems problems at scale."
    response = DraftResponse(answer=example, evidence_ids=["acme-eng"])

    assert validate_draft(response, facts, style_examples=(example,)) is None


def test_validate_draft_accepts_an_adapted_answer_not_near_verbatim():
    facts = {"acme-eng": "Engineer -- Acme"}
    example = "I am drawn to solving hard distributed-systems problems at scale."
    response = DraftResponse(
        answer="I want to keep building reliable backend infrastructure.",
        evidence_ids=["acme-eng"],
    )

    assert validate_draft(response, facts, style_examples=(example,)) is not None


def test_validate_draft_rejects_a_leaked_company_name_from_a_style_example():
    facts = {"acme-eng": "Engineer -- Acme"}
    example = "I've always admired Initech's engineering culture."
    response = DraftResponse(
        answer="I've always admired Initech's engineering culture and want to help it grow.",
        evidence_ids=["acme-eng"],
    )

    assert validate_draft(
        response, facts, style_examples=(example,), job=JobContext(company="Globex")
    ) is None


def test_validate_draft_allows_the_current_companys_name():
    facts = {"acme-eng": "Engineer -- Acme"}
    example = "I've always admired Acme's engineering culture."
    response = DraftResponse(
        answer="I've always admired Acme's focus on reliability.",
        evidence_ids=["acme-eng"],
    )

    assert validate_draft(
        response, facts, style_examples=(example,), job=JobContext(company="Acme")
    ) is not None


class _StubClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def complete_json(self, prompt, schema):
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def grounded_profile() -> Profile:
    return make_profile(
        work_history=[
            ProfileEntry(id="acme-eng", title="Engineer", organization="Acme", summary="Built widgets."),
        ],
    )


def test_grounded_drafter_returns_draft_on_valid_response(grounded_profile):
    client = _StubClient(response=DraftResponse(answer="I built widgets at Acme.", evidence_ids=["acme-eng"]))
    drafter = GroundedDrafter(client, grounded_profile)

    draft = drafter(FormQuestion(id="q1", label="Tell us about yourself", required=False, kind="text"))

    assert draft is not None
    assert draft.text == "I built widgets at Acme."


def test_grounded_drafter_returns_none_on_llm_error(grounded_profile):
    client = _StubClient(error=LLMError("boom"))
    drafter = GroundedDrafter(client, grounded_profile)

    draft = drafter(FormQuestion(id="q1", label="Tell us about yourself", required=False, kind="text"))

    assert draft is None


def test_grounded_drafter_returns_none_without_any_profile_facts():
    client = _StubClient(response=DraftResponse(answer="anything", evidence_ids=[]))
    drafter = GroundedDrafter(client, make_profile())

    draft = drafter(FormQuestion(id="q1", label="Tell us about yourself", required=False, kind="text"))

    assert draft is None
