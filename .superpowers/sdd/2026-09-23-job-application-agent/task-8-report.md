# Task 8 implementation report

## Result

Implemented application processing and status transitions on `feat/job-application-agent`.

Implementation commit: `9357a3bb2e675655486774cc6704d9bc6796f01e` (`feat: coordinate application workflow with safe deferrals`).

## TDD record

- **RED — missing workflow:** after writing the orchestration tests first, `env UV_CACHE_DIR=/private/tmp/task8-uv-cache uv run pytest tests/test_workflow.py -q` failed during collection with `ModuleNotFoundError: No module named 'jobapply.workflow'`.
- **GREEN — initial state machine:** once `workflow.py` was implemented, the first 10 workflow cases passed. The first run exposed test fixture construction errors, which were corrected in the tests before proceeding.
- **RED/GREEN — resume upload verification:** the new workflow test initially showed a required resume proceeding to submission when the adapter reported the upload unverified. Added `verify_resume_upload(page, question_id)` to the adapter contract and implementation; the workflow test then passed.
- **RED/GREEN — adapter/context failures:** a test for a question-reading failure initially observed `failed`; pre-submit operational errors now persist as `deferred`, while errors after submit begins persist as `uncertain`.
- **RED/GREEN — validated generated answers:** a supported free-text draft initially remained deferred because the required custom question was classified as unknown by the overall gate. After draft citations, length, and Jev support validate, the workflow treats only that generated question as resolved and still runs `submission_decision()` against the remaining required questions. The success case then passed.

## Verification

All commands used the temporary uv cache because the default cache at `/Users/ashwin/.cache/uv` is inaccessible in this sandbox.

- Focused: `env UV_CACHE_DIR=/private/tmp/task8-uv-cache uv run pytest tests/test_workflow.py tests/test_ats_adapters.py -q` — **28 passed, 27 skipped**.
- Full suite: `env UV_CACHE_DIR=/private/tmp/task8-uv-cache uv run pytest -q` — **138 passed, 27 skipped**.
- `git diff --check` — passed before commit.

The 27 skips are browser-backed ATS adapter tests. Local Chrome launched and then aborted with `SIGABRT`; Playwright also reported `EPERM` while attempting to terminate the process. This prevented execution of the newly added Greenhouse and Lever browser fixture checks in this environment. The checks are committed and cover empty, selected, wrong-control, and cleared file inputs when a local browser is available. Workflow tests use fake pages, adapters, and model clients; no live network, browser, or model calls were made by them.

## Files changed

- `src/jobapply/workflow.py` — URL claiming and duplicate outcomes; supported ATS selection; deterministic mapping; restricted Jev structured routing; free-text generation with job context and evidence validation; separate submission policy gate; dry-run; explicit submit confirmation; and history persistence for terminal outcomes.
- `src/jobapply/ats/base.py` — adapter protocol and selected-file verification for required resume uploads.
- `tests/test_workflow.py` — fake-driven orchestration coverage for supported and unknown ATS, required profile values, Jev defer, generation failure and success, duplicates, dry-run, confirmed/uncertain submissions, explicit uncertain retry, resume verification, sensitive-value isolation, and exception status.
- `tests/test_ats_adapters.py` — local Greenhouse and Lever fixture coverage for resume upload verification.

## Review notes and concerns

- `HistoryStore.claim()` and `finish()` remain the source of duplicate and terminal-state rules. Uncertain applications are not retried unless `retry_uncertain=True` is passed.
- Dry-run fills only; it never calls `submit()`.
- Required resume questions are excluded from the policy’s unresolved list only after adapter verification confirms a file is selected.
- Jev receives only candidate facts whose values match available select options, and sensitive profile facts are excluded. Missing sensitive answers are not delegated to Jev or generation.
- Generated answers require structural citation/length checks and Jev claim support; Jev support does not replace the overall submission policy gate.
- Browser execution remains unverified in this sandbox because Chrome aborts at startup. The focused browser tests should be run in an environment where Playwright can launch a local browser.

## Review fix round 1/5

### Findings addressed

- Added deterministic sensitive-question detection for work authorization and immigration/sponsorship, disability/medical/accommodation, veteran/military, criminal history, and other protected-class categories. Exact mapped answers backed by explicit sensitive profile fields are still accepted; other sensitive questions defer before Jev or text generation and do not pass evidence to either.
- Refresh non-claimed batch outcomes from `HistoryStore` after claimed URLs finish. This resolves canonical duplicates, including tracking-parameter variants, to the first URL's persisted terminal result. Browser startup/launch failure finalization uses the same refresh.

### TDD and verification

- **RED:** alternate sponsorship and conviction free-text cases both incorrectly submitted; a production-path batch with two tracking variants returned `processing` for the duplicate.
- **GREEN:** focused workflow and adapter suite: `env UV_CACHE_DIR=/private/tmp/task8-review-uv-cache uv run pytest tests/test_workflow.py tests/test_ats_adapters.py -q` — **32 passed, 27 skipped**.
- Added startup-failure duplicate coverage to verify failure finalization refreshes from history.
- Full suite: `env UV_CACHE_DIR=/private/tmp/task8-review-uv-cache uv run pytest -q` — **142 passed, 27 skipped**.
- No network, browser, or model calls were used. The 27 browser-backed adapter tests remain skipped in this environment.

### Plan correction follow-up

- Updated brief interface includes `mapping.py` and `tests/test_mapping.py`, with `is_sensitive_question(question) -> bool` as the mapping API.
- Added focused mapping coverage for alternate sponsorship and conviction wording, explicit protected-class and gender wording, and ordinary application questions. The initial run caught the missing generic “protected class” wording; added that deterministic pattern.
- Mapping, workflow, and ATS adapter suites: **60 passed, 27 skipped**. Full suite: **149 passed, 27 skipped**.

## Review fix round 2/5

### Finding addressed

- Expanded criminal-history wording detection to include arrest, charge, offense/offence, incarceration, plea, conviction variants, and expungement wording. Exact mapped answers still resolve through explicit sensitive profile aliases before the conservative wording guard; unmatched sensitive questions defer before Jev or generation.

### TDD and verification

- **RED:** new alternate-wording tests failed for arrest, charge/offense, incarceration, and plea phrasing in both mapping and workflow behavior. The workflow cases incorrectly reached submission; expungement was already covered through its reference to conviction.
- **GREEN:** `env UV_CACHE_DIR=/private/tmp/task8-round2-uv-cache uv run pytest tests/test_mapping.py tests/test_workflow.py -q` — **57 passed**.
- Focused mapping/workflow/adapter suite: `env UV_CACHE_DIR=/private/tmp/task8-round2-uv-cache uv run pytest tests/test_mapping.py tests/test_workflow.py tests/test_ats_adapters.py -q` — **70 passed, 27 skipped**.
- Full suite: `env UV_CACHE_DIR=/private/tmp/task8-round2-uv-cache uv run pytest -q` — **159 passed, 27 skipped**.
- No network, browser, or model calls were made. Browser-backed adapter tests remain skipped in this environment.
