"""Persistence for answers the user gave live, so the same question is not asked twice.

Kept separate from the hand-maintained profile so that file stays exactly what the
user wrote. Never consulted for a protected question -- those resolve only from an
explicit `profile.declared` entry, by design (see `answers.py`).

Keyed by the question's own normalized label -- a rephrasing of the same question
is treated as a different question. That's a real limitation (you may get asked
"Why do you want to work here?" again after answering "What interests you about
this company?"), but it's simple to understand and to hand-edit the YAML file
directly. Revisit only if that turns out to actually be annoying in practice.
"""

from pathlib import Path

import yaml

from artemis.forms import QuestionKind
from artemis.mapping import normalize_label


class LearnedAnswers:
    """A flat label -> answer store, persisted as YAML."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._answers: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if not isinstance(data, dict):
            return
        for raw_label, value in data.items():
            if isinstance(raw_label, str) and isinstance(value, (str, int, float, bool)):
                self._answers[raw_label] = str(value)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(self._answers, file, sort_keys=False)
        tmp_path.replace(self.path)

    def get(
        self, label: str, kind: QuestionKind | None = None, options: list[str] | tuple[str, ...] = ()
    ) -> str | None:
        """The learned answer for a label, or None on a miss or a stale mismatch.

        A select's stored answer that is no longer among the current options is
        treated as a miss rather than handed to the caller: a free-text answer
        learned before a form's options changed (or before a select replaced a
        text field) must not be forced into a control it no longer fits.
        """

        answer = self._answers.get(normalize_label(label))
        if answer is None:
            return None
        if kind == "select" and options:
            normalized_options = {normalize_label(option) for option in options}
            if normalize_label(answer) not in normalized_options:
                return None
        return answer

    def record(self, label: str, value: str) -> None:
        self._answers[normalize_label(label)] = value
        self._write()
