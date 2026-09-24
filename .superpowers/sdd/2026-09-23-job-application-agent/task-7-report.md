# Task 7: Greenhouse and Lever browser adapters

## Result

Implemented a shared Playwright adapter contract and Greenhouse/Lever adapters. Form reading and filling are separate from explicit submission; fill() never clicks a submit control. Pages and submit targets require HTTPS and a platform allowlisted hostname. Ambiguous fields, unsupported/custom controls, login walls, and CAPTCHA cause a safe deferral; unconfirmed submits return uncertain.

## TDD evidence

- RED: uv run pytest tests/test_ats_adapters.py -q failed during collection with ModuleNotFoundError: No module named 'jobapply.ats', confirming the adapters were missing before implementation.
- GREEN/local verification: after implementation, adapter tests reported 2 passed, 13 skipped. The two runnable checks cover HTTPS host recognition and suffix spoof rejection. Browser interaction tests skipped because no browser process could start in this environment (details below).
- Full suite: uv run pytest -q reported 112 passed, 13 skipped.
- Static checks: Python compileall and git diff --check passed.
- Fixtures and browser requests are local-only; no external ATS pages are loaded in tests.

## Browser installation and runtime

- The first normal install attempt failed to create Playwright's default cache at /Users/ashwin/Library/Caches/ms-playwright (EPERM).
- Retrying with PLAYWRIGHT_BROWSERS_PATH=/tmp/trailhead-playwright reached the download step, but failed because DNS could not resolve cdn.playwright.dev (getaddrinfo ENOTFOUND).
- The existing /Applications/Google Chrome.app was tried as a local browser. Playwright launch ended in TargetClosedError; Chrome aborted with SIGABRT, and cleanup reported kill EPERM.
- Consequently, the browser integration cases are present but were not executed here. The focused and full test commands exit successfully with those 13 cases skipped.

## Files

- src/jobapply/ats/__init__.py
- src/jobapply/ats/base.py
- src/jobapply/ats/greenhouse.py
- src/jobapply/ats/lever.py
- tests/fixtures/ats/greenhouse.html
- tests/fixtures/ats/lever.html
- tests/test_ats_adapters.py

## Review notes and concerns

- Submission checks both the form action and submit button formaction before clicking. Confirmation is based on visible success text; missing confirmation stays uncertain.
- Answers resolve to a unique control by id/name or exact accessible label. File upload accepts only an existing local regular file; no upload is attempted automatically.
- Confidence: moderate. Host matching, syntax, and the rest of the repository suite passed, but DOM interaction behavior still needs a browser-enabled run because this sandbox could not launch one.

## Commit

Commit: feat: automate supported Greenhouse and Lever forms.

## Review fix round 1/5

### Changes

- Restricted adapter recognition to the fixture-backed HTTPS hosts: `boards.greenhouse.io`, `job-boards.greenhouse.io`, and `jobs.lever.co`. Arbitrary subdomains and `greenhouse.com`/`lever.com` hosts are rejected.
- Added a submit preflight over required form controls. Unsupported, invalid, unanswered, or required file controls without a selected file return `uncertain` before any click.
- Restricted the candidate submit control to native submit-capable buttons and inputs, then matched its accessible name against “submit” or “apply”.
- Added a main-frame navigation route guard during submission. Off-allowlist navigation requests are aborted, and an off-host page can never be reported as confirmed.
- Kept filling and submission separate; no CAPTCHA or login bypass was added.

### TDD and verification evidence

- RED: `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run pytest tests/test_ats_adapters.py -q` initially reported 3 failed, 3 passed, and 16 skipped. Failures demonstrated arbitrary Greenhouse/Lever subdomains being accepted and a required file being treated as ready without a selected file. The first invocation without the task-local uv cache was blocked by cache permissions before pytest started.
- GREEN/focused: `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run pytest tests/test_ats_adapters.py -q` reported 6 passed, 17 skipped.
- Full suite: `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run pytest -q` reported 116 passed, 17 skipped.
- The 17 browser tests were skipped because the local browser could not start in this environment. Their behavior remains unverified here; no live ATS calls were made.
- `git diff --check` passed. Compile checks and self-review are recorded for this fix round before commit.

### Remaining concern

Confidence is moderate. Exact-host checks and the pure required-control readiness rules have runnable coverage, and the full Python suite passes. Native submit selection and redirect interception have fixture-backed browser tests but still need a browser-enabled run.

### Commit

Commit message: `fix: harden ATS submission review findings`.

## Review fix round 2/5

### Changes

- Submission now invokes `read_questions()` before its readiness check, so custom-widget detection in the extraction path also stops submission before a click. Unknown required native controls are rejected by the readiness check.
- The preflight includes both native `required` and `aria-required="true"` form controls.
- Readiness checks require nonempty values for required text/select controls, checked state for required checkboxes, and a selected file for required file controls. Unsupported or invalid required controls remain uncertain.
- Preserved the exact HTTPS host allowlist, native submit control selector, and off-allowlist main-frame navigation guard from round 1.

### TDD and verification evidence

- RED: New pure readiness tests initially failed against the old helper because empty required values and unchecked checkboxes were accepted. The first focused run reported 3 failed, 7 passed, and 19 skipped.
- GREEN/focused: `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run pytest tests/test_ats_adapters.py -q` reported 10 passed, 19 skipped.
- Full suite: `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run pytest -q` reported 120 passed, 19 skipped.
- `UV_CACHE_DIR=/tmp/trailhead-uv-cache uv run python -m compileall -q src/jobapply/ats` and `git diff --check` passed.
- Browser cases for ARIA-required controls and custom widgets are included but skipped because the local browser cannot launch in this environment. No live ATS calls were made.

### Self-review and concerns

- Checked that preflight exits before submit-button discovery/click for deferred custom widgets and any unready required native or ARIA-required control. Prior host validation, native submit selection, and redirect interception remain unchanged.
- Confidence: moderate. Pure readiness and full-suite coverage pass; Playwright DOM behavior remains unverified until run in a browser-enabled environment.

### Commit

Pending.
