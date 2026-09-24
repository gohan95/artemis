"""Shared types describing an application form question and its answer."""

from dataclasses import dataclass, field
from typing import Literal

QuestionKind = Literal["text", "email", "phone", "select", "checkbox", "file", "unknown"]


@dataclass(frozen=True)
class FormQuestion:
    """A single control on a supported application form."""

    id: str
    label: str
    required: bool
    kind: QuestionKind
    options: list[str] = field(default_factory=list)
    max_length: int | None = None


@dataclass(frozen=True)
class FieldAnswer:
    """A value to write into one form control, and how it was resolved."""

    question_id: str
    value: str
    method: Literal["profile", "learned", "user"]
