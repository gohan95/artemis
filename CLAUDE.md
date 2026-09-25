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
uv run artemis setup                      # interactively create/edit the profile
```

No lint/format/typecheck tooling configured.

## Architecture

A personal CLI that fills, and (opted in) submits, job applications on a small set of ATS platforms,
given a hand-curated list of URLs. Job discovery/crawling remains out of scope. An LLM (Gemini) is
used in two narrow, human-reviewed places: drafting free-text application answers and extracting
structured profile fields from a resume during setup — see "Product boundary" below.

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

### LLM drafting (`llm.py`, `drafting.py`, `profile_setup.py`)

`llm.py` is the only module allowed to import a vendor SDK (`google-genai`). Everything
else calls the `LLMClient` protocol's one method, `complete_json(prompt, schema)`, which
returns a validated pydantic model or raises `LLMError`. Swapping providers means writing
one new class with that method and changing what `build_client` returns — no other module
changes. Settings are provider-neutral (`ARTEMIS_LLM_API_KEY`, `ARTEMIS_LLM_MODEL`). Copy
`.env.example` to `.env` (gitignored) and set `ARTEMIS_LLM_API_KEY` there — `Settings`
loads it automatically.

`drafting.py` drafts free-text application answers, offered to `ask_user` as an editable
default the person can accept, edit, or clear — never applied without being seen. A draft
must be evidence-grounded: the model must cite specific profile facts by id
(`drafting.profile_facts`), and `validate_draft` discards any draft citing an id that
doesn't exist or leaving `answer` empty. Sensitive questions are never drafted — the
existing fail-closed exclusion in `pipeline.py`'s per-question loop sits upstream of the
drafting call, not the other way around. Any failure (no API key, network error, quota,
malformed response, no grounding) silently falls through to the plain blank prompt; a
drafting problem must never fail a run.

`profile_setup.py` backs `artemis setup`: extracts text from a resume PDF (`pypdf`) and
asks the LLM to read it into structured fields, which the person reviews and edits before
anything is written to `data/profile.yaml`. Without an API key, setup falls back to fully
manual entry.

## Product boundary

Job discovery, ranking, a GUI, and arbitrary (non-allowlisted) websites are out of scope.
Sensitive/legally-significant profile facts are never inferred — no exception, including from
an LLM. The LLM's role is deliberately narrow: it drafts free-text answers and extracts resume
fields for a human to review, and never sees, drafts, or infers an answer to a sensitive
question; never auto-submits; and never fills a field the person hasn't seen. Don't expand host
support, or widen what the LLM is allowed to touch, without confirming the boundary is meant
to change.
