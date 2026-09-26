"""Tests for label normalization, canonical question types, and the structural
guarantee that no canonical type can ever apply to a protected question."""

from artemis.mapping import (
    CANONICAL_PHRASES,
    CANONICAL_TYPES,
    canonical_form,
    canonical_type,
    is_protected_question,
)


def test_no_canonical_phrase_is_a_protected_question():
    """Structural enforcement, not discipline: every seed phrase that maps to a
    canonical type must be unreachable through is_protected_question. If this
    ever fails, a protected question has leaked into the classification system,
    which would let it be matched/reused the same way an ordinary essay is."""

    for phrase in CANONICAL_PHRASES:
        assert is_protected_question(phrase) is False, f"{phrase!r} is flagged protected"


def test_canonical_types_is_closed_and_matches_phrase_targets():
    assert set(CANONICAL_PHRASES.values()) <= CANONICAL_TYPES


def test_canonical_form_strips_punctuation_and_enumerators():
    assert canonical_form("Why do you want to work here?") == "why do you want to work here"
    assert canonical_form("1. Desired start date") == "desired start date"
    assert canonical_form("Notice period (optional)") == "notice period"
    assert canonical_form("  What is your notice period?  ") == "what is your notice period"


def test_canonical_type_matches_seed_phrasing():
    assert canonical_type("Why do you want to work here?") == "why_company"
    assert canonical_type("What interests you about this role?") == "why_role"


def test_canonical_type_none_on_miss():
    assert canonical_type("What is your favorite color?") is None
