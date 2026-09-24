"""Offline-first evaluation of Jev's structured application-question decisions."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
from typing import Any

from jobapply.jev import DecisionAnswer, JevClient
from jobapply.mapping import _PROFILE_ALIASES, _RESUME_ALIASES, _SENSITIVE_ALIASES, normalize_label
from jobapply.settings import Settings

FIXTURE_PATH = Path(__file__).parents[1] / "tests" / "fixtures" / "jev" / "application_questions.jsonl"


def load_fixtures(path: Path = FIXTURE_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def exact_rule(record: dict[str, Any]) -> str:
    """Apply exact aliases only; ambiguous or unsupported questions defer."""
    label = normalize_label(record["question"])
    profile_alias = _PROFILE_ALIASES.get(label)
    sensitive_alias = _SENSITIVE_ALIASES.get(label)
    if profile_alias is not None:
        candidate = f"profile.{profile_alias[0]}"
    elif sensitive_alias is not None:
        candidate = f"profile.sensitive_answers.{sensitive_alias}"
    elif label in _RESUME_ALIASES:
        return "defer"
    else:
        return "defer"
    available_ids = {
        fact.get("id") for fact in record["state"].get("facts", [])
        if isinstance(fact, dict)
    }
    return candidate if candidate in available_ids and candidate in record["options"] else "defer"


def evaluate_fixtures(records: list[dict[str, Any]], decisions: dict[str, DecisionAnswer], *, latencies: dict[str, float] | None = None, threshold: float = 0.98) -> dict[str, Any]:
    actual = {row["id"]: decisions.get(row["id"], DecisionAnswer("defer")) for row in records}
    errors = [row for row in records if actual[row["id"]].value != row["expected_choice"]]
    correct = len(records) - len(errors)
    false_sensitive = [row["id"] for row in records if row["category"] == "sensitive" and actual[row["id"]].value not in {"defer", "sensitive_missing"}]
    disagreements = [
        {"id": row["id"], "model": actual[row["id"]].value, "rule": exact_rule(row)}
        for row in records if actual[row["id"]].value != exact_rule(row)
    ]
    confusion = Counter(f'{row["expected_choice"]} -> {actual[row["id"]].value}' for row in records)
    bins: Counter[str] = Counter()
    for answer in actual.values():
        if answer.confidence is not None:
            floor = min(int(answer.confidence * 10) * 10, 90)
            bins[f"{floor:02d}-{floor + 10:02d}%"] += 1
    high_errors = [row["id"] for row in errors if actual[row["id"]].confidence is not None and actual[row["id"]].confidence >= threshold]
    return {
        "fixture_count": len(records),
        "accuracy": correct / len(records) if records else 0.0,
        "correct_count": correct,
        "error_count": len(errors),
        "confusion_counts": dict(sorted(confusion.items())),
        "confidence_bins": dict(sorted(bins.items())),
        "deferral_rate": sum(answer.value == "defer" for answer in actual.values()) / len(records) if records else 0.0,
        "sensitive_false_answer_count": len(false_sensitive),
        "sensitive_false_answer_ids": false_sensitive,
        "rule_disagreement_count": len(disagreements),
        "disagreement_examples": disagreements,
        "high_confidence_error_count": len(high_errors),
        "high_confidence_error_ids": high_errors,
        "mean_latency_ms": sum((latencies or {}).values()) / len(latencies) if latencies else None,
        "sensitive_policy_gate_passed": not false_sensitive,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="opt in to API calls on synthetic fixtures")
    args = parser.parse_args(argv)
    if not args.live:
        print("Refusing to run Jev evaluation without explicit --live.", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if not settings.jev_api_key or not settings.jev_model:
        print("JEV_API_KEY and JEV_MODEL are required with --live.", file=sys.stderr)
        return 2
    records = load_fixtures()
    decisions: dict[str, DecisionAnswer] = {}
    latencies: dict[str, float] = {}
    client = JevClient(settings)
    try:
        for row in records:
            started = time.perf_counter()
            decisions[row["id"]] = client.decide(
                row["state"],
                {row["id"]: {"text": row["question"], "type": "choice", "options": row["options"]}},
            )[row["id"]]
            latencies[row["id"]] = (time.perf_counter() - started) * 1000
    finally:
        client.close()
    print(json.dumps(evaluate_fixtures(records, decisions, latencies=latencies, threshold=settings.jev_min_confidence), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
