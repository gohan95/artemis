"""Tests for tier-B (LLM-assisted) question classification: validation
discipline, graceful degradation, and the protected-question refusal."""

import pytest

from artemis.llm import LLMError
from artemis.question_identity import _ClassificationResponse, classify_question


class _StubClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def complete_json(self, prompt, schema):
        if self._error is not None:
            raise self._error
        return self._response


def test_classify_question_accepts_high_confidence_known_type():
    client = _StubClient(response=_ClassificationResponse(canonical_type="why_role", confidence="high"))

    assert classify_question("What draws you to this position?", client) == "why_role"


def test_classify_question_rejects_low_confidence():
    client = _StubClient(response=_ClassificationResponse(canonical_type="why_role", confidence="low"))

    assert classify_question("Something ambiguous", client) is None


def test_classify_question_rejects_type_outside_closed_list():
    client = _StubClient(response=_ClassificationResponse(canonical_type="made_up_type", confidence="high"))

    assert classify_question("Something odd", client) is None


def test_classify_question_returns_none_on_llm_error():
    client = _StubClient(error=LLMError("boom"))

    assert classify_question("Why do you want this job?", client) is None


def test_classify_question_never_calls_client_for_a_protected_question():
    class _ExplodingClient:
        def complete_json(self, prompt, schema):
            raise AssertionError("must not be called for a protected question")

    assert classify_question("Are you a veteran?", _ExplodingClient()) is None


@pytest.mark.parametrize("confidence", ["high", "low"])
def test_classify_question_accepts_null_type(confidence):
    client = _StubClient(response=_ClassificationResponse(canonical_type=None, confidence=confidence))

    assert classify_question("Some genuinely novel question", client) is None
