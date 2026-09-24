# Task 10 report: local ATS end-to-end verification

## Scope

Added CLI-to-workflow-to-SQLite-to-Playwright scenarios using the real Greenhouse and Lever adapters. Jev and text generation are deterministic fakes. Browser requests are routed to the checked-in HTML fixtures; each scenario asserts exactly one local document GET and no other request, including no form submission request.

Scenarios cover profile-backed submission, a generated evidence-supported answer, missing sensitive-answer deferral before model calls, duplicate rerun prevention, unsupported required-question deferral, and uncertain status when submit has no observable confirmation.

## TDD and verification

- RED: the first profile-backed scenario failed with `sensitive_answer_missing`. The nested Greenhouse label included its select option text, preventing exact matching of the supported sensitive-question label.
- GREEN: separated that fixture label from its select, then reran all six scenarios successfully.
- Focused browser run: `uv run --cache-dir /private/tmp/trailhead-uv-cache --offline --no-sync pytest tests/test_end_to_end.py -q` — **6 passed, 0 failed, 0 skipped** when run outside the restricted process sandbox.
- Focused sandbox run: **6 skipped**. Exact skip reason: `local browser could not start: BrowserType.launch: Target page, context or browser has been closed`; Chrome exits with `signal=SIGABRT`, and Playwright logs `exception while trying to kill process: Error: kill EPERM`. The skips are explicit and are not counted as passes.
- Full suite: `uv run --cache-dir /private/tmp/trailhead-uv-cache --offline --no-sync pytest -q` — **203 passed, 1 failed**. The remaining failure is `tests/test_ats_adapters.py::test_fill_rejects_ambiguous_matching_fields` (`Failed: DID NOT RAISE AdapterDeferred`). That test and the adapter implementation were not changed in this task.
- No network access or real ATS submission was used. The final browser-enabled focused run passed all scenarios against fixture responses; each asserted only its local fixture document was requested.
- `git diff --check` passed.

## Files

- `tests/test_end_to_end.py` — six real CLI/workflow/history/adapter scenarios and deterministic fakes.
- `tests/fixtures/ats/greenhouse.html` — supported sensitive label and local scenario states for generated, unsupported, successful, and unconfirmed outcomes.
- `tests/fixtures/ats/lever.html` — matching local scenario states for generated and unsupported questions and confirmation outcomes.

## Limitations

Browser scenarios skip under the restricted process sandbox because Chromium aborts at startup; they were also run successfully with browser access enabled. The full suite is not entirely green due to the single unrelated ambiguous-field adapter test noted above.

## Review/final verification fix — round 1/5

- Root cause: `_answer_locator()` compared raw field identities exactly, so a question ID such as `email` found `name="email"` but did not count other controls identified by case- or whitespace-different names, IDs, or accessible labels. This let ambiguous fills proceed.
- Regression RED: expanded `test_fill_rejects_ambiguous_matching_fields` to cover normalized matching through `name`, `id`, associated labels, `aria-label`, and `aria-labelledby`. With local Chromium enabled, all five cases failed as expected because `AdapterDeferred` was not raised.
- Fix: normalize the question ID and all candidate identities with whitespace collapsing and `casefold()`, and include associated and ARIA label text when counting matches. Multiple matching controls now defer.
- Focused GREEN: `uv run --cache-dir /private/tmp/trailhead-uv-cache --offline --no-sync pytest tests/test_ats_adapters.py::test_fill_rejects_ambiguous_matching_fields -q` — **5 passed, 0 failed, 0 skipped** with local Chromium enabled. The restricted-sandbox attempt skipped all five because Chromium could not start; these are not counted as passes.
- Full suite: `uv run --cache-dir /private/tmp/trailhead-uv-cache --offline --no-sync pytest -q` — **208 passed, 0 failed, 0 skipped** with local Chromium enabled.
- Safety and review: offline test execution; browser pages in the regression are routed to inline local HTML. No real ATS requests or submissions. `git diff --check` passed; reviewed changes are limited to the matching root cause, regression coverage, and this report.
