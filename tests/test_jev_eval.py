from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from jobapply.jev import DecisionAnswer
FIXTURES = Path(__file__).parent / "fixtures" / "jev" / "application_questions.jsonl"
SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "jev_eval.py"
SPEC = spec_from_file_location("jev_eval", SCRIPT_PATH)
jev_eval = module_from_spec(SPEC)
SPEC.loader.exec_module(jev_eval)
evaluate_fixtures = jev_eval.evaluate_fixtures
load_fixtures = jev_eval.load_fixtures


def test_labeled_fixture_set_covers_required_categories_and_minimum_count():
    records = load_fixtures(FIXTURES)

    assert len(records) >= 40
    assert {record["category"] for record in records} >= {
        "contact", "employment", "education", "sensitive", "free_text", "unknown"
    }
    assert all(record["expected_action"] in {"select", "free_text", "sensitive_missing", "defer"} for record in records)


def test_mocked_evaluation_reports_metrics_and_sensitive_false_answers():
    records = load_fixtures(FIXTURES)
    decisions = {
        record["id"]: DecisionAnswer(record["expected_choice"], 0.99, {record["expected_choice"]: 0.99})
        for record in records
    }

    report = evaluate_fixtures(records, decisions)

    assert report["fixture_count"] == len(records)
    assert report["accuracy"] == 1.0
    assert report["sensitive_false_answer_count"] == 0
    assert report["deferral_rate"] >= 0
    assert "rule_disagreement_count" in report


def test_sensitive_false_answers_and_high_confidence_errors_disable_routing():
    records = load_fixtures(FIXTURES)
    decisions = {
        row["id"]: DecisionAnswer(row["expected_choice"], 0.99, {})
        for row in records
    }
    sensitive = next(row for row in records if row["category"] == "sensitive")
    decisions[sensitive["id"]] = DecisionAnswer("profile.email", 0.99, {})

    report = evaluate_fixtures(records, decisions, threshold=0.98)

    assert report["sensitive_false_answer_count"] == 1
    assert report["high_confidence_error_count"] == 1
    assert report["sensitive_policy_gate_passed"] is False


def test_sensitive_answer_is_supported_only_by_explicit_fixture_evidence():
    records = load_fixtures(FIXTURES)
    explicit = next(
        row for row in records
        if row["category"] == "sensitive" and row["expected_evidence_field_ids"]
    )
    decisions = {
        row["id"]: DecisionAnswer(row["expected_choice"], 0.99, {})
        for row in records
    }
    decisions[explicit["id"]] = DecisionAnswer(
        explicit["expected_choice"], 0.99, {}
    )

    report = evaluate_fixtures(records, decisions)

    assert report["sensitive_false_answer_count"] == 0
    assert report["sensitive_policy_gate_passed"] is True

    decisions[explicit["id"]] = DecisionAnswer("profile.email", 0.99, {})
    report = evaluate_fixtures(records, decisions)

    assert report["sensitive_false_answer_count"] == 1
    assert report["sensitive_policy_gate_passed"] is False


def test_default_cli_refuses_before_network_and_live_requires_explicit_flag(monkeypatch, capsys):
    monkeypatch.setattr(jev_eval, "JevClient", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("client constructed")))
    assert jev_eval.main([]) == 2
    assert "--live" in capsys.readouterr().err
