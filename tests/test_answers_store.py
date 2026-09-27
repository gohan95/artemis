"""Tests for the learned-answers store: atomic writes and select staleness."""

from pathlib import Path

import yaml

from artemis.answers_store import LearnedAnswers


def test_round_trip_through_a_fresh_store(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    store = LearnedAnswers(path)
    store.record("Desired start date", "Immediately")

    reloaded = LearnedAnswers(path)
    assert reloaded.get("Desired start date") == "Immediately"


def test_write_is_atomic_no_partial_file_left_behind(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    store = LearnedAnswers(path)
    store.record("Desired start date", "Immediately")

    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert path.is_file()


def test_lookup_is_case_and_whitespace_insensitive(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    path.write_text(yaml.safe_dump({"desired start date": "Immediately"}), encoding="utf-8")

    store = LearnedAnswers(path)

    assert store.get("Desired   Start Date") == "Immediately"


def test_select_answer_no_longer_among_current_options_is_a_miss(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Notice period", "Immediately")

    assert store.get("Notice period", kind="select", options=["ASAP", "2 weeks", "1 month"]) is None


def test_select_answer_still_among_current_options_resolves(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Notice period", "2 weeks")

    assert store.get("Notice period", kind="select", options=["ASAP", "2 weeks", "1 month"]) == "2 weeks"
