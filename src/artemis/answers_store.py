"""Persistence for answers the user gave live, so the same question is not asked twice.

Kept separate from the hand-maintained profile so that file stays exactly what the
user wrote. Never used for sensitive questions — those must come from an explicit
`profile.sensitive_answers` entry, by design (see `answers.py`).
"""

from pathlib import Path

import yaml

from artemis.mapping import normalize_label


class LearnedAnswers:
    """A normalized-label -> answer-text map, persisted as YAML."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._answers: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        with self.path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, (str, int, float, bool))
        }

    def get(self, label: str) -> str | None:
        return self._answers.get(normalize_label(label))

    def set(self, label: str, value: str) -> None:
        self._answers[normalize_label(label)] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(self._answers, file, sort_keys=True)
