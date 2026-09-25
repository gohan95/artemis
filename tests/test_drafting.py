"""Tests for evidence-grounded answer drafting: grounding corpus, validation,
and graceful degradation when the LLM fails or returns nothing usable."""

from pathlib import Path

import pytest

from artemis.drafting import DraftResponse, GroundedDrafter, profile_facts, validate_draft
from artemis.forms import FormQuestion
from artemis.llm import LLMError
from artemis.profile import Profile, ProfileEntry


def make_profile(**overrides) -> Profile:
    defaults = dict(resume_path=Path(__file__))
    defaults.update(overrides)
    return Profile(**defaults)


def test_profile_facts_includes_work_history_education_skills_background():
    profile = make_profile(
        work_history=[
            ProfileEntry(id="acme-eng", title="Engineer", organization="Acme", summary="Built widgets."),
        ],
        education=[ProfileEntry(id="mit-cs", title="BS CS", organization="MIT")],
        skills=["Python", "SQL"],
        background="Looking for backend roles.",
    )

    facts = profile_facts(profile)

    assert facts["acme-eng"] == "Engineer -- Acme -- Built widgets."
    assert facts["mit-cs"] == "BS CS -- MIT"
    assert facts["skills"] == "Python, SQL"
    assert facts["background"] == "Looking for backend roles."


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
