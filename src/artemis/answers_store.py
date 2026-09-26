"""Persistence for answers the user gave live, so the same question is not asked twice.

Kept separate from the hand-maintained profile so that file stays exactly what the
user wrote. Never consulted for a protected question — those resolve only from an
explicit `profile.declared` entry, by design (see `answers.py`).

Schema v2: a list of records rather than a flat label->value dict, so a record can
carry a kind, options, provenance, and the set of raw labels it was learned under.
A v1 file (a bare dict of scalar values, no top-level `version` key) is read and
converted in memory on load; it is not rewritten until the next write, at which
point the original is preserved as `<path>.v1.bak` before the v2 file is written.
Migration never guesses a canonical type for a v1 entry -- a wrong guess here would
put a stale answer under a new key silently; entries stay `keyed_by: "raw_label"`
until re-learned or explicitly reclassified.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from artemis.forms import QuestionKind
from artemis.mapping import canonical_form, canonical_type, normalize_label

Provenance = Literal["typed", "edited_draft", "accepted_draft"]
KeyedBy = Literal["canonical_type", "raw_label"]

_SCHEMA_VERSION = 2


@dataclass
class AnswerRecord:
    key: str
    keyed_by: KeyedBy
    answer: str
    kind: QuestionKind | None = None
    options: tuple[str, ...] = ()
    labels_seen: tuple[str, ...] = ()
    provenance: Provenance = "typed"
    company: str | None = None
    role: str | None = None
    created_at: str = ""
    last_used_at: str = ""
    reuse_count: int = 0

    def to_yaml(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "keyed_by": self.keyed_by,
            "answer": self.answer,
            "kind": self.kind,
            "options": list(self.options),
            "labels_seen": list(self.labels_seen),
            "provenance": self.provenance,
            "company": self.company,
            "role": self.role,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "reuse_count": self.reuse_count,
        }

    @classmethod
    def from_yaml(cls, data: dict[str, Any]) -> "AnswerRecord | None":
        key = data.get("key")
        answer = data.get("answer")
        if not isinstance(key, str) or not isinstance(answer, str):
            return None
        keyed_by = data.get("keyed_by")
        if keyed_by not in ("canonical_type", "raw_label"):
            keyed_by = "raw_label"
        provenance = data.get("provenance")
        if provenance not in ("typed", "edited_draft", "accepted_draft"):
            provenance = "typed"
        options = data.get("options")
        labels_seen = data.get("labels_seen")
        return cls(
            key=key,
            keyed_by=keyed_by,
            answer=answer,
            kind=data.get("kind"),
            options=tuple(options) if isinstance(options, list) else (),
            labels_seen=tuple(labels_seen) if isinstance(labels_seen, list) else (),
            provenance=provenance,
            company=data.get("company") if isinstance(data.get("company"), str) else None,
            role=data.get("role") if isinstance(data.get("role"), str) else None,
            created_at=data.get("created_at") if isinstance(data.get("created_at"), str) else "",
            last_used_at=data.get("last_used_at") if isinstance(data.get("last_used_at"), str) else "",
            reuse_count=data.get("reuse_count") if isinstance(data.get("reuse_count"), int) else 0,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LearnedAnswers:
    """A store of answers the user has given live, keyed by canonical type when
    one is known and by raw label otherwise, persisted as YAML."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._records: dict[str, AnswerRecord] = {}
        # Negative cache: a canonical_form(label) the classifier looked at and
        # declined to type. Consulted before paying for another model call.
        self._label_index: dict[str, str | None] = {}
        self._migrated_from_v1 = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if not isinstance(data, dict):
            return

        if "version" not in data:
            self._load_v1(data)
            return

        for raw in data.get("answers", []) or []:
            if not isinstance(raw, dict):
                continue
            record = AnswerRecord.from_yaml(raw)
            if record is not None:
                self._records[record.key] = record

        label_index = data.get("label_index", {}) or {}
        if isinstance(label_index, dict):
            for raw_label, value in label_index.items():
                if isinstance(raw_label, str) and (value is None or isinstance(value, str)):
                    self._label_index[raw_label] = value

    def _load_v1(self, data: dict[str, Any]) -> None:
        """A bare dict of scalar values with no `version` key: today's flat
        normalized-label -> answer-text format. Converted in memory; never
        guesses a canonical type, so every entry stays raw-label-keyed."""

        for raw_label, value in data.items():
            if not isinstance(raw_label, str) or not isinstance(value, (str, int, float, bool)):
                continue
            self._records[raw_label] = AnswerRecord(
                key=raw_label,
                keyed_by="raw_label",
                answer=str(value),
                labels_seen=(raw_label,),
                provenance="typed",
                created_at=_now(),
                last_used_at=_now(),
            )
        self._migrated_from_v1 = bool(self._records)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._migrated_from_v1 and self.path.is_file():
            backup = self.path.with_suffix(self.path.suffix + ".v1.bak")
            if not backup.is_file():
                self.path.replace(backup)
            self._migrated_from_v1 = False

        data = {
            "version": _SCHEMA_VERSION,
            "answers": [record.to_yaml() for record in self._records.values()],
            "label_index": dict(self._label_index),
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(data, file, sort_keys=False)
        tmp_path.replace(self.path)

    def _key_for(self, label: str) -> tuple[str, KeyedBy]:
        """The lookup key for a label: its canonical type if one is known
        (tier A, then the negative/positive label_index from a prior tier-B
        call), else the label's own normalized form."""

        form = canonical_form(label)
        found = canonical_type(label)
        if found is None:
            found = self._label_index.get(form)
        if found is not None:
            return found, "canonical_type"
        return normalize_label(label), "raw_label"

    def get(
        self, label: str, kind: QuestionKind | None = None, options: list[str] | tuple[str, ...] = ()
    ) -> str | None:
        """The learned answer for a label, or None on a miss or a stale mismatch.

        A select's stored answer that is no longer among the current options is
        treated as a miss rather than handed to the caller: a free-text answer
        learned before a form's options changed (or before a select replaced a
        text field) must not be forced into a control it no longer fits.
        """

        key, keyed_by = self._key_for(label)
        record = self._records.get(key)
        if record is None and keyed_by == "canonical_type":
            # The label matches a canonical type on lookup, but the answer may
            # have been recorded before classification existed (e.g. migrated
            # from v1) and so still sits under its raw label. Fall back rather
            # than stranding it.
            record = self._records.get(normalize_label(label))
        if record is None:
            return None
        if kind == "select" and options:
            normalized_options = {normalize_label(option) for option in options}
            if normalize_label(record.answer) not in normalized_options:
                return None
        return record.answer

    def record(
        self,
        label: str,
        value: str,
        *,
        kind: QuestionKind | None = None,
        options: list[str] | tuple[str, ...] = (),
        provenance: Provenance = "typed",
        company: str | None = None,
        role: str | None = None,
    ) -> None:
        key, keyed_by = self._key_for(label)
        existing = self._records.get(key)
        labels_seen = tuple({*existing.labels_seen, label}) if existing else (label,)
        now = _now()
        self._records[key] = AnswerRecord(
            key=key,
            keyed_by=keyed_by,
            answer=value,
            kind=kind,
            options=tuple(options),
            labels_seen=labels_seen,
            provenance=provenance,
            company=company,
            role=role,
            created_at=existing.created_at if existing else now,
            last_used_at=now,
            reuse_count=existing.reuse_count if existing else 0,
        )
        self._write()

    def note_reuse(self, label: str) -> None:
        key, _ = self._key_for(label)
        existing = self._records.get(key)
        if existing is None:
            return
        self._records[key] = replace(
            existing, reuse_count=existing.reuse_count + 1, last_used_at=_now()
        )
        self._write()

    def note_classification(self, label: str, canonical: str | None) -> None:
        """Cache a tier-B classification result (including a negative one) so
        the same novel phrasing is never re-sent to the model twice."""

        self._label_index[canonical_form(label)] = canonical
        self._write()

    def style_examples(self, canonical: str, limit: int = 3) -> list[str]:
        """Up to `limit` prior answers for `canonical`, most recently used
        first, restricted to answers the user actually wrote or edited
        themselves -- never `accepted_draft`, which is the model's own prose
        the user merely didn't bother to change. See drafting.py stage 4."""

        matches = [
            record
            for record in self._records.values()
            if record.keyed_by == "canonical_type"
            and record.key == canonical
            and record.provenance in ("typed", "edited_draft")
        ]
        matches.sort(key=lambda record: record.last_used_at, reverse=True)
        return [record.answer for record in matches[:limit]]
