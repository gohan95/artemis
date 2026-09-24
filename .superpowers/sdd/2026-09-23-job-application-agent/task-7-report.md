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
