"""Shared normalized application form contracts."""

from dataclasses import dataclass
from typing import Literal


QuestionKind = Literal["text", "email", "phone", "select", "checkbox", "unknown"]


@dataclass(frozen=True)
class FormQuestion:
    """A question extracted from an application form."""

    id: str
    label: str
    required: bool
    kind: QuestionKind
    options: list[str]
    max_length: int | None


@dataclass(frozen=True)
class FieldAnswer:
    """A field value tied to the profile evidence that supports it."""

    question_id: str
    value: str
    evidence_ids: list[str]
    method: str
