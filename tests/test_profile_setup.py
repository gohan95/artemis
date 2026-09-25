"""Tests for the pure helpers behind `artemis setup`: merging profile data with
the right precedence, generating stable entry ids, and round-tripping YAML."""

from pathlib import Path

import pytest

from artemis.profile import Profile, load_profile
from artemis.profile_setup import merge_profile, write_profile


def make_profile(**overrides) -> Profile:
    defaults = dict(resume_path=Path(__file__), full_name="Riley Example")
    defaults.update(overrides)
    return Profile(**defaults)


def test_merge_profile_answers_win_over_extracted_and_existing():
    existing = make_profile(email="old@example.test")
    extracted = {"email": "extracted@example.test", "phone": "555-0100"}
    answers = {"email": "typed@example.test"}

    merged = merge_profile(existing, extracted, answers)

    assert merged.email == "typed@example.test"
    assert merged.phone == "555-0100"


def test_merge_profile_extracted_fills_gap_but_keeps_existing_when_absent():
    existing = make_profile(location="Sunnyvale, CA")
    extracted = {"phone": "555-0100"}

    merged = merge_profile(existing, extracted, {})

    assert merged.location == "Sunnyvale, CA"
    assert merged.phone == "555-0100"


def test_merge_profile_with_no_existing_profile_uses_extracted_and_answers():
    extracted = {"full_name": "Extracted Name"}
    answers = {"resume_path": str(Path(__file__)), "email": "typed@example.test"}

    merged = merge_profile(None, extracted, answers)

    assert merged.full_name == "Extracted Name"
    assert merged.email == "typed@example.test"


def test_merge_profile_generates_stable_deduped_entry_ids():
    extracted = {
        "work_history": [
            {"organization": "Acme", "title": "Engineer"},
            {"organization": "Acme", "title": "Engineer"},
        ]
    }

    merged = merge_profile(None, extracted, {"resume_path": str(Path(__file__))})

    ids = [entry.id for entry in merged.work_history]
    assert ids == ["acme-engineer", "acme-engineer-2"]


def test_merge_profile_empty_extracted_values_do_not_overwrite_existing():
    existing = make_profile(background="Looking for backend roles.")
    extracted = {"background": ""}

    merged = merge_profile(existing, extracted, {})

    assert merged.background == "Looking for backend roles."


def test_write_profile_round_trips_through_load_profile(tmp_path: Path):
    resume = tmp_path / "resume.pdf"
    resume.write_text("not a real pdf, just needs to exist")
    profile = make_profile(resume_path=resume, email="riley@example.test")
    profile_path = tmp_path / "profile.yaml"

    write_profile(profile, profile_path)
    loaded = load_profile(profile_path)

    assert loaded.full_name == "Riley Example"
    assert loaded.email == "riley@example.test"


def test_write_profile_backs_up_existing_file(tmp_path: Path):
    resume = tmp_path / "resume.pdf"
    resume.write_text("not a real pdf, just needs to exist")
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text("full_name: Old Name\nresume_path: resume.pdf\n")

    write_profile(make_profile(resume_path=resume, full_name="New Name"), profile_path)

    backup = profile_path.with_suffix(profile_path.suffix + ".bak")
    assert backup.is_file()
    assert "Old Name" in backup.read_text()
    assert load_profile(profile_path).full_name == "New Name"
