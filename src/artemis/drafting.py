"""Draft free-text application answers, grounded in the user's own profile facts.

A draft is only ever a suggestion the pipeline hands to `ask_user` as an editable
default -- it is never applied without the user seeing and confirming it (see
pipeline.py). Grounding is enforced here, not trusted from the model: every
`evidence_ids` entry the model returns must match a real profile fact, or the
draft is discarded and the caller falls back to today's blank prompt.
"""

import sys
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from artemis.forms import FormQuestion
from artemis.llm import LLMClient, LLMError
from artemis.profile import Profile


class DraftResponse(BaseModel):
    """The shape the model must return -- handed to LLMClient.complete_json."""

    answer: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class Draft:
    text: str
    evidence_ids: tuple[str, ...]


class DraftAnswer(Protocol):
    def __call__(self, question: FormQuestion) -> Draft | None: ...


def profile_facts(profile: Profile) -> dict[str, str]:
    """The grounding corpus: {fact_id: text} an answer may cite."""

    facts: dict[str, str] = {}
    for section in (profile.work_history, profile.education):
        for entry in section:
            parts = [entry.title, entry.organization, entry.summary]
            text = " -- ".join(part for part in parts if part)
            if text:
                facts[entry.id] = text
    if profile.skills:
        skills = profile.skills if isinstance(profile.skills, list) else list(profile.skills.keys())
        if skills:
            facts["skills"] = ", ".join(str(skill) for skill in skills)
    if profile.background:
        facts["background"] = profile.background
    return facts


def build_prompt(question: FormQuestion, facts: dict[str, str]) -> str:
    fact_lines = "\n".join(f"- {fact_id}: {text}" for fact_id, text in facts.items())
    length_hint = f" Keep it under {question.max_length} characters." if question.max_length else ""
    return (
        "You are drafting one answer to a job application question, on behalf of "
        "the candidate described by the facts below. Write a concise, first-person "
        "answer using only these facts -- do not invent employers, skills, dates, "
        "or achievements that are not listed.\n\n"
        f"Facts:\n{fact_lines}\n\n"
        f"Question: {question.label}\n\n"
        "Return JSON with `answer` (the drafted text) and `evidence_ids` (the ids "
        "of every fact above that the answer actually draws on). If none of the "
        "facts are relevant to this question, return an empty `answer` and an "
        f"empty `evidence_ids` list.{length_hint}"
    )


def validate_draft(response: DraftResponse, facts: dict[str, str]) -> Draft | None:
    if not response.answer.strip():
        return None
    if not response.evidence_ids:
        return None
    if not all(fact_id in facts for fact_id in response.evidence_ids):
        return None
    return Draft(text=response.answer.strip(), evidence_ids=tuple(response.evidence_ids))


class GroundedDrafter:
    def __init__(self, client: LLMClient, profile: Profile):
        self._client = client
        self._facts = profile_facts(profile)

    def __call__(self, question: FormQuestion) -> Draft | None:
        if not self._facts:
            return None
        prompt = build_prompt(question, self._facts)
        try:
            response = self._client.complete_json(prompt, DraftResponse)
        except LLMError as error:
            print(f"LLM call failed: {error}", file=sys.stderr)
            return None
        return validate_draft(response, self._facts)
