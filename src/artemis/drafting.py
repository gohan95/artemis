"""Draft free-text application answers, grounded in the user's own profile facts.

A draft is only ever a suggestion the pipeline hands to `ask_user` as an editable
default -- it is never applied without the user seeing and confirming it (see
pipeline.py). Grounding is enforced here, not trusted from the model: every
`evidence_ids` entry the model returns must match a real profile fact, or the
draft is discarded and the caller falls back to today's blank prompt.

Style examples (prior answers to the same canonical question type) and job
context (company/role/requirements, from `ats.base.read_job_context`) are both
optional prompt input, but neither is ever addable to `profile_facts` and
`validate_draft`'s check against it is unchanged by their presence. If either
were citable, a draft could satisfy grounding by citing only the model's own
earlier prose or an unverified claim about the employer -- see the module's
test suite, particularly `test_validate_draft_rejects_a_draft_citing_only_a_style_example`,
which is the regression guard for this property.
"""

import difflib
import sys
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from artemis.answers_store import LearnedAnswers
from artemis.ats.base import JobContext
from artemis.forms import FormQuestion
from artemis.llm import LLMClient, LLMError
from artemis.mapping import canonical_type
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
    def __call__(
        self, question: FormQuestion, job: JobContext | None = None, company_notes: str = ""
    ) -> Draft | None: ...


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
    if profile.goals:
        facts["goals"] = profile.goals
    return facts


def build_prompt(
    question: FormQuestion,
    facts: dict[str, str],
    job: JobContext | None = None,
    style_examples: tuple[str, ...] = (),
    company_notes: str = "",
) -> str:
    fact_lines = "\n".join(f"- {fact_id}: {text}" for fact_id, text in facts.items())
    length_hint = f" Keep it under {question.max_length} characters." if question.max_length else ""

    if job and (job.company or job.role or job.requirements or company_notes):
        company_note = (
            "You have not been given the specific company's name -- do not invent or "
            "assume one." if not job.company else ""
        )
        role_note = (
            "You have not been given the specific role's title -- do not invent or "
            "assume one." if not job.role else ""
        )
        job_lines = "\n".join(
            line for line in (
                f"Company: {job.company}" if job.company else "",
                f"Role: {job.role}" if job.role else "",
                f"Requirements: {job.requirements}" if job.requirements else "",
                f"Specific things the candidate wants mentioned: {company_notes}" if company_notes else "",
            ) if line
        )
        job_section = (
            "Job context (background only -- do not cite this as evidence, and do not "
            "invent additional details about the company or role beyond what's given "
            f"here):\n{job_lines}\n{company_note}{role_note}\n\n"
        )
    else:
        job_section = (
            "You have not been given any information about the specific company or "
            "role beyond the question text itself -- do not invent or assume anything "
            "about them (e.g. products, mission, culture, reputation).\n\n"
        )

    style_section = ""
    if style_examples:
        examples = "\n".join(f'- "{example}"' for example in style_examples)
        style_section = (
            "Your previous answers to similar questions, to imitate voice and "
            "recurring themes only -- do NOT copy phrasing, and do NOT treat these "
            f"as facts you may cite:\n{examples}\n\n"
        )

    return (
        "You are drafting one answer to a job application question, on behalf of "
        "the candidate described by the facts below. Write a concise, first-person "
        "answer using only these facts -- do not invent employers, skills, dates, "
        f"or achievements that are not listed.\n\n{job_section}{style_section}"
        f"Facts:\n{fact_lines}\n\n"
        f"Question: {question.label}\n\n"
        "Return JSON with `answer` (the drafted text) and `evidence_ids` (the ids "
        "of every fact above that the answer actually draws on). If none of the "
        "facts are relevant to this question, return an empty `answer` and an "
        f"empty `evidence_ids` list.{length_hint}"
    )


_NEAR_VERBATIM_RATIO = 0.9


def validate_draft(
    response: DraftResponse,
    facts: dict[str, str],
    style_examples: tuple[str, ...] = (),
) -> Draft | None:
    """Reject an ungrounded draft, or one that's near-verbatim replay of a
    style example rather than an answer adapted to the current question.
    Everything else -- a style example naming a different company, an
    unwanted tone, an awkward phrase -- is left for the person to catch when
    they review the draft; that review is the actual safety net here, and
    validation isn't the place to re-build it in code."""

    if not response.answer.strip():
        return None
    if not response.evidence_ids:
        return None
    if not all(fact_id in facts for fact_id in response.evidence_ids):
        return None
    answer = response.answer.strip()
    if _too_similar_to_any(answer, style_examples):
        return None
    return Draft(text=answer, evidence_ids=tuple(response.evidence_ids))


def _too_similar_to_any(answer: str, style_examples: tuple[str, ...]) -> bool:
    return any(
        difflib.SequenceMatcher(None, answer, example).ratio() >= _NEAR_VERBATIM_RATIO
        for example in style_examples
    )


class GroundedDrafter:
    def __init__(self, client: LLMClient, profile: Profile, learned: LearnedAnswers | None = None):
        self._client = client
        self._facts = profile_facts(profile)
        self._learned = learned

    def __call__(
        self, question: FormQuestion, job: JobContext | None = None, company_notes: str = ""
    ) -> Draft | None:
        if not self._facts:
            return None
        style_examples: tuple[str, ...] = ()
        if self._learned is not None:
            canonical = canonical_type(question.label)
            if canonical is not None:
                style_examples = tuple(self._learned.style_examples(canonical))
        prompt = build_prompt(
            question, self._facts, job=job, style_examples=style_examples, company_notes=company_notes
        )
        try:
            response = self._client.complete_json(prompt, DraftResponse)
        except LLMError as error:
            print(f"LLM call failed: {error}", file=sys.stderr)
            return None
        return validate_draft(response, self._facts, style_examples)
