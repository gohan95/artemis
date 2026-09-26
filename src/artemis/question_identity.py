"""Tier B of question-type classification: an LLM fallback for a label that
`mapping.canonical_type` (deterministic, tier A) doesn't recognize.

Only ever called on a tier-A miss, and only for a question already confirmed
NOT protected -- classification is a form of derivation, and a protected
question must never be typed, matched, or otherwise touched by this module.
Any failure (no API key, network error, malformed response, a type outside
the closed list, low confidence) is treated as "no match" -- callers fall
back exactly as if no client were configured at all.
"""

import sys
from typing import Literal

from pydantic import BaseModel

from artemis.llm import LLMClient, LLMError
from artemis.mapping import CANONICAL_TYPES, is_protected_question


class _ClassificationResponse(BaseModel):
    canonical_type: str | None
    confidence: Literal["high", "low"]


def classify_question(label: str, client: LLMClient) -> str | None:
    """Ask the model to map a label to one of the closed canonical types.

    Returns None on any failure, a type outside CANONICAL_TYPES, or "low"
    confidence -- validated here, not trusted from the model, the same
    discipline as drafting.validate_draft.
    """

    if is_protected_question(label):
        return None

    prompt = (
        "Classify this job-application form question into exactly one of the "
        "following canonical types, or null if none fit well:\n"
        f"{', '.join(sorted(CANONICAL_TYPES))}\n\n"
        f"Question: {label}\n\n"
        "Return JSON with `canonical_type` (one of the types above, or null) "
        "and `confidence` (\"high\" or \"low\")."
    )
    try:
        response = client.complete_json(prompt, _ClassificationResponse)
    except LLMError as error:
        print(f"LLM call failed: {error}", file=sys.stderr)
        return None

    if response.confidence != "high":
        return None
    if response.canonical_type not in CANONICAL_TYPES:
        return None
    return response.canonical_type
