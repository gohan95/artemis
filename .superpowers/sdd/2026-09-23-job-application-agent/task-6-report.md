# Task 6 report: evidence-referenced free-text answers

## Status

Implemented and committed on `feat/job-application-agent`. The worktree is clean.

## Implementation

- Added typed `SupportedClaim` and `DraftAnswer` contracts and an OpenAI `TextGenerator` using the official Python SDK Responses API with Pydantic structured output. The selected model comes from `OPENAI_MODEL` via settings.
- Generation receives only supplied facts that match question/job-context terms. Sensitive-answer evidence is filtered unless the question explicitly asks for that field. Resume page evidence is reduced to a clipped, relevant excerpt; a full page is never passed as generation evidence.
- Final answer text is rendered deterministically by joining claims. Draft validation defers for empty answers, blank claims, missing/unknown citations, and answers exceeding the question's `max_length`.
- Added separate `JevClient.check_claim_support(...)` outcomes (`supported`, `unsupported`, `defer`) with confidence and the claim's validated evidence IDs. Support checks use only the cited facts. A policy helper evaluates each claim and requires support confidence of at least `0.98`; negative, invalid, or uncertain results fail closed.
- Support evaluations are logged with status, confidence, and evidence IDs. They remain evaluation signals, not proof or standalone submit authorization. Existing `decide()` choice restrictions remain unchanged.
- OpenAI and Jev clients were faked in tests; no live network or model calls were made.

## TDD evidence

- RED: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py -q` reached the new tests and failed as expected: valid draft validation deferred, and the generation client path raised `NotImplementedError` in the temporary scaffold (3 failed, 4 passed).
- GREEN: after implementation, the focused suite passed (49 tests), then passed again after added support logging/credential checks (50 tests).
- Self-review RED: the new regression test for a short, single-sentence resume page failed because the complete page text was sent (`1 failed`).
- Self-review GREEN: the clipped-excerpt fix passed the generation tests (8 passed), followed by the final full suite (105 passed).

The first `uv` invocation could not initialize its default home cache due filesystem permissions; redirecting `UV_CACHE_DIR` to `/private/tmp/uv-cache-trailhead` resolved the test-run setup without network access.

## Verification

- Focused: `uv run pytest tests/test_generation.py tests/test_policy.py tests/test_jev.py -q` — 50 passed (before the final privacy regression test).
- Final full suite: `uv run pytest -q` — 105 passed.
- `git diff --check` — clean.
- Final branch: `feat/job-application-agent`; worktree clean.

## Commits

- `d0974eb feat: draft evidence-referenced application answers`
- `a8d35c9 fix: clip resume evidence before generation`

## Files changed

- `src/jobapply/generation.py` (new)
- `src/jobapply/policy.py`
- `src/jobapply/jev.py`
- `tests/test_generation.py` (new)
- `tests/test_policy.py`
- `tests/test_jev.py`

## Concerns and limits

- Evidence relevance is lexical and intentionally conservative; semantically relevant facts without matching terms may be omitted, causing generation to defer.
- Resume evidence is keyed at page granularity. The model sees a clipped excerpt but cites the page's evidence ID; this retains the repository's current evidence identity convention.
- The Jev support endpoint was validated with offline fakes only. Its live server response behavior was not exercised, as requested.
- A model support result is fallible and is not a factual proof or an independent application-submit authorization.

## Review fix round 1/5

- Replaced the split structural/support paths with `validate_draft()` as the single validation boundary. It checks nonempty claims, nonblank claim text, known evidence IDs, and the stricter of the question limit and any explicit limit before calling Jev.
- The boundary checks every claim against only its cited facts, requires the configured Jev confidence threshold, and defers on missing Jev, exceptions, invalid results, negative/defer outcomes, or low confidence. A successful validation returns a `ValidatedAnswer`; `render_answer()` rejects a raw `DraftAnswer`.
- Validation only establishes an evidence-checked draft. Existing overall submission policy remains the later submission gate, and `JevClient.decide()` choices are unchanged.
- RED: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py tests/test_policy.py -q` — 11 failed, 18 passed. Failures showed raw drafts rendered, structural-only validation returned submit, and the required support-aware API was absent.
- Additional RED: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py::test_explicit_max_length_cannot_weaken_question_limit -q` — 1 failed because the weaker explicit limit masked the question's stricter limit.
- Additional RED: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py::test_valid_structure_defers_when_jev_returns_no_support_result -q` — 1 failed because a missing support result raised instead of deferring.
- GREEN: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py tests/test_policy.py tests/test_jev.py -q` — 56 passed.
- Full suite: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest -q` — 110 passed. `git diff --check` — clean.
- No live network or model calls were made. Jev behavior is covered with offline fakes; its live response contract remains unverified.
- Commit: `fix: require support validation before rendering drafts`.

## Review fix round 2/5

- Removed the publicly constructible `ValidatedAnswer` and `render_answer()` path. `Decision` no longer carries any answer token that callers could forge and pass to a renderer.
- Replaced `validate_draft()` with one `validate_and_render_answer(...)` operation. It checks the claim list and text, validates every citation ID, enforces the question's maximum length, checks each claim against only its cited facts via Jev, and requires the configured minimum confidence. It returns `(Decision(action="submit"), rendered_text)` only after all checks pass; every defer result is paired with `None` text.
- Existing submission policy remains separate and unchanged. Tests use fake Jev/OpenAI clients only; no live network behavior was introduced.
- RED: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py tests/test_policy.py -q` — 13 failed, 18 passed. The failures were assertions that the new validation-and-render boundary was missing.
- GREEN focused: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest tests/test_generation.py tests/test_policy.py tests/test_jev.py -q` — 56 passed.
- GREEN full suite: `UV_CACHE_DIR=/private/tmp/uv-cache-trailhead uv run pytest -q` — 110 passed. `git diff --check` — clean.
- No live network or model calls were made. Jev support behavior remains verified with offline fakes only.
