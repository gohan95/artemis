# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is a personal, single-user project, not a product. Favor simple, direct code over
production-grade abstraction (config layers, plugin systems, generic extensibility) unless the task
at hand actually needs it. Keep comments minimal — only for genuinely non-obvious invariants, not
restating what the code already says.

## Commands

```bash
uv sync                                   # install dependencies
uv run patchright install chrome          # one-time browser binary setup
uv run pytest -q                          # full test suite (offline)
uv run pytest tests/test_ats.py -q        # a single test file
uv run pytest tests/test_ats.py::test_greenhouse_reads_native_and_combobox_questions

uv run artemis apply urls.txt             # fill applications, no submit (the default)
uv run artemis apply urls.txt --submit    # actually send filled applications
uv run artemis history                    # list recorded application outcomes
uv run artemis validate-profile           # sanity-check data/profile.yaml
```

No lint/format/typecheck tooling configured.

## Architecture

A personal CLI that fills, and (opted in) submits, job applications on a small set of ATS platforms,
given a hand-curated list of URLs. Job discovery/crawling and an LLM are both explicitly out of scope
— see "Product boundary" below.

### Core flow (`pipeline.py`, `ats/base.py`)

`ApplicationPipeline._process_url` is the per-URL state machine:

1. `HistoryStore.claim(url)` — atomic SQLite claim, dedups on canonicalized URL.
2. Pick the first `ATSAdapter` whose `supports_url` matches (exact host allowlist).
3. `wait_until_ready` → `read_questions` → `answers.resolve_question` per field: resume alias → exact
   profile alias → sensitive-question check → learned-answers store → ask the user live and cache it.
4. `adapter.fill` (never submits) → if `--submit`, `adapter.submit` → `confirm_submission`.
5. `HistoryStore.finish` records the status; `OnFilled` gets one look at the page before it closes.

`BaseATSAdapter` (`ats/base.py`) holds nearly all the browser-facing logic — reading/filling forms,
CAPTCHA and login-wall detection, submit confirmation. `GreenhouseAdapter`/`LeverAdapter`/`AshbyAdapter`
are just per-vendor class-attribute overrides (`allowed_hosts`, `form_selector`, `submit_selector`).

Whenever a value can't be determined with confidence, the whole application defers rather than
guessing (`AdapterDeferred`). This shows up throughout: ambiguous field matches, multiple submit
buttons, CAPTCHAs, login walls, and unsupported controls all defer instead of best-effort guessing.
Sensitive/legally-significant questions (work authorization, sponsorship, disability, veteran status,
criminal history — `mapping.py`) are answered only from an explicit `profile.sensitive_answers` value,
never inferred and never routed through the live-prompt/learned path. `--submit` defaults off.

### Answer resolution (`answers.py`, `mapping.py`, `answers_store.py`)

`mapping.py`'s alias tables are exact-match only — add an alias only after observing that exact
phrasing on a real posting. Sensitive-question detection is the opposite: deliberately over-inclusive
regexes, since over-triggering there just causes an extra defer.

`LearnedAnswers` caches what the user typed in response to a live prompt for an unmapped question —
it's a cache of prior answers, not a generated answer bank.

### History (`history.py`)

SQLite-backed. `submitted` is a terminal, never-re-claimable status. `uncertain` (a submit was clicked
but not confirmed) is re-claimable, since it's not known whether the application actually went through.

### Browser session (`browser.py`, `pacing.py`)

Uses `patchright`, a drop-in Playwright fork that patches automation signals at the CDP/launch-args
layer. Runs through a persistent Chrome profile rather than a throwaway browser. Don't add
user-agent/header overrides here — a spoofed value disagrees with the browser's real Client
Hints/TLS/WebGL signals and is more detectable, not less; the module only sets
locale/timezone/color_scheme, derived deterministically from the profile directory.

`pacing.py` provides optional human-like typing/click pacing (`Pacer` protocol), defaulting to a
no-op `NullPacer` everywhere it's constructed.

### Receipts (`receipts.py`)

Every `filled`/`submitted`/`uncertain` outcome gets a screenshot + JSON sidecar under
`data/receipts/<url-hash>/` (gitignored).

## Product boundary

Job discovery, ranking, a GUI, and arbitrary (non-allowlisted) websites are out of scope.
Sensitive/legally-significant profile facts are never inferred. Don't reintroduce LLM-based
generation or expand host support without confirming the boundary is meant to change.
