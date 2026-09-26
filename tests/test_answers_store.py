"""Tests for the learned-answers store: v1 migration, atomic writes, kind/options
validation, canonical-type keying, and negative classification caching."""

from pathlib import Path

import yaml

from artemis.answers_store import LearnedAnswers


def test_round_trip_through_a_fresh_store(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    store = LearnedAnswers(path)
    store.record("Desired start date", "Immediately", kind="text")

    reloaded = LearnedAnswers(path)
    assert reloaded.get("Desired start date") == "Immediately"


def test_write_is_atomic_no_partial_file_left_behind(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    store = LearnedAnswers(path)
    store.record("Desired start date", "Immediately")

    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert path.is_file()


def test_v1_flat_file_is_read_and_resolves_by_raw_label(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    path.write_text(yaml.safe_dump({"desired start date": "Immediately"}), encoding="utf-8")

    store = LearnedAnswers(path)

    assert store.get("Desired start date") == "Immediately"


def test_v1_file_is_backed_up_on_first_v2_write(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    path.write_text(yaml.safe_dump({"desired start date": "Immediately"}), encoding="utf-8")

    store = LearnedAnswers(path)
    store.record("Another question", "An answer")

    backup = path.with_suffix(path.suffix + ".v1.bak")
    assert backup.is_file()
    assert yaml.safe_load(backup.read_text()) == {"desired start date": "Immediately"}

    data = yaml.safe_load(path.read_text())
    assert data["version"] == 2


def test_v1_migration_never_guesses_a_canonical_type(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    path.write_text(
        yaml.safe_dump({"why do you want to work here?": "Because the mission resonates with me."}),
        encoding="utf-8",
    )

    store = LearnedAnswers(path)
    store.record("A second, unrelated question", "some value")

    data = yaml.safe_load(path.read_text())
    migrated = next(r for r in data["answers"] if r["key"] == "why do you want to work here?")
    assert migrated["keyed_by"] == "raw_label"


def test_select_answer_no_longer_among_current_options_is_a_miss(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Notice period", "Immediately", kind="text")

    assert store.get("Notice period", kind="select", options=["ASAP", "2 weeks", "1 month"]) is None


def test_select_answer_still_among_current_options_resolves(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Notice period", "2 weeks", kind="select", options=["ASAP", "2 weeks", "1 month"])

    assert store.get("Notice period", kind="select", options=["ASAP", "2 weeks", "1 month"]) == "2 weeks"


def test_canonical_type_match_across_rephrasing(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Why do you want to work here?", "Because the mission resonates with me.")

    assert store.get("What interests you about our company") == "Because the mission resonates with me."


def test_negative_classification_is_cached(tmp_path: Path):
    path = tmp_path / "learned.yaml"
    store = LearnedAnswers(path)
    store.note_classification("What is your favorite color?", None)

    data = yaml.safe_load(path.read_text())
    assert data["label_index"]["what is your favorite color"] is None


def test_note_reuse_increments_count(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Desired start date", "Immediately")

    store.note_reuse("Desired start date")
    store.note_reuse("Desired start date")

    data = yaml.safe_load(store.path.read_text())
    [record] = data["answers"]
    assert record["reuse_count"] == 2


def test_style_examples_excludes_accepted_drafts(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    store.record("Why do you want to work here?", "typed answer", provenance="typed")

    examples = store.style_examples("why_company")
    assert examples == ["typed answer"]

    # Overwriting with an accepted (unedited) draft must remove it from the
    # style corpus -- it's the model's own prose, not evidence of voice.
    store.record("Why do you want to work here?", "model's own words", provenance="accepted_draft")

    assert store.style_examples("why_company") == []


def test_style_examples_empty_when_nothing_recorded(tmp_path: Path):
    store = LearnedAnswers(tmp_path / "learned.yaml")
    assert store.style_examples("why_company") == []
