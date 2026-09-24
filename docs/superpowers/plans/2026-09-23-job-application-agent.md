# Job Application Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user Python CLI that completes curated job applications on Greenhouse and Lever, uses Jev for evaluated structured decisions and OpenAI for evidence-grounded free text, and submits only when required answers pass validation.

**Architecture:** Keep control flow and exact mappings in Python. Isolate ATS browser adapters, Jev decisions, and OpenAI text generation behind small interfaces; a workflow coordinator combines their results with profile data and local SQLite history. Use YAML for the editable profile, local resume files as evidence, and HTML fixtures for browser tests so ordinary tests never submit to real employers.

**Tech Stack:** Python 3.12, uv, Typer, Pydantic, PyYAML, pypdf, Playwright, SQLite, httpx, OpenAI Python SDK with the Responses API, pytest, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-23-job-application-agent-design.md`

## Global Constraints

- “The initial product is single-user” and “does not implement multi-tenancy.”
- “The user provides a list of curated job application URLs.”
- “Job discovery, opportunity ranking, a graphical interface, and broad support for arbitrary websites are outside v1.”
- “The CLI may submit applications autonomously when required information is present and validated.”
- “If the agent cannot determine an answer reliably, it defers that application.”
- “Sensitive or legally significant questions—including work authorization, sponsorship, disability, veteran status, and criminal history—may be answered only from an explicit profile value. If that value is absent, the agent must defer rather than infer.”
- “Jev's typed output and confidence are not proof of correctness and do not independently authorize submission.”
- “Claims must be supported by supplied source material.”
- “Re-running a job list does not submit an application already recorded as submitted.”

## Review Focus

- **Unrecognized or deceptive form labels:** map to a known field only with evidence; otherwise defer. Test unknown labels and prompt-injection-like label text.
- **Sensitive question with missing profile value:** defer without inference. Test every sensitive field category in the profile policy.
- **A page that changes or cannot confirm submission:** record `uncertain`, then avoid an automatic retry. Test navigation failure and missing confirmation.
- **Duplicate URLs and equivalent URLs with tracking parameters:** canonicalize carefully and avoid a second submission. Test duplicate inputs and query-string variants.
- **Generated answer with absent or invalid source references:** reject the draft and defer. Test unknown evidence IDs, overlong content, and model/API failure.

---

## File Structure

- `pyproject.toml` — package metadata, runtime dependencies, and test configuration.
- `src/jobapply/cli.py` — Typer commands and command-line exit behavior.
- `src/jobapply/settings.py` — environment-backed settings and secret configuration.
- `src/jobapply/profile.py` — profile schema, YAML loading, resume text extraction, and fact identifiers.
- `src/jobapply/history.py` — SQLite run/application history and duplicate protection.
- `src/jobapply/forms.py` — shared application question, answer, and outcome types.
- `src/jobapply/ats/base.py` — ATS adapter protocol and shared browser lifecycle.
- `src/jobapply/ats/greenhouse.py` — Greenhouse page recognition, question extraction, filling, and submit confirmation.
- `src/jobapply/ats/lever.py` — Lever page recognition, question extraction, filling, and submit confirmation.
- `src/jobapply/mapping.py` — deterministic mappings and fixed-answer validation.
- `src/jobapply/jev.py` — Jev HTTP client and typed structured-decision parsing.
- `scripts/jev_eval.py` — opt-in live Jev evaluation against the labeled fixture set and deterministic baseline.
- `src/jobapply/generation.py` — OpenAI Responses API adapter and evidence-referenced draft schema.
- `src/jobapply/policy.py` — sensitive-field rules, evidence checks, and submit/defer decision.
- `src/jobapply/workflow.py` — per-URL state machine and coordination of adapters, models, and history.
- `tests/fixtures/ats/` — minimal Greenhouse and Lever application pages served locally in browser tests.
- `tests/` — unit, adapter, CLI, and end-to-end workflow tests.
- `examples/profile.example.yaml` — documented profile shape with fictional values only.
- `.gitignore` — exclude real profile, resume, API keys, browser state, local databases, and generated artifacts.

## Tasks

### Task 1: Create the Python package and safe local configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/jobapply/__init__.py`
- Create: `src/jobapply/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings.from_env() -> Settings` with `profile_path`, `history_path`, `jev_api_key`, `jev_model`, `jev_min_confidence` (default `0.98`), `openai_api_key`, `openai_model`, and `browser_headless` fields. API keys and model names are optional at load time; use of the relevant adapter validates required settings.

- [ ] **Step 1: Write failing settings tests** for defaults, environment overrides, and absent secrets.

```python
def test_settings_reads_model_names_and_keeps_missing_keys_optional(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    settings = Settings.from_env()
    assert settings.openai_model == "test-model"
    assert settings.openai_api_key is None
```

- [ ] **Step 2: Run `uv run pytest tests/test_settings.py -q`** and confirm the tests fail because package/settings do not exist.
- [ ] **Step 3: Add package metadata and settings.** Declare Python `>=3.12`; runtime dependencies `typer`, `pydantic`, `pydantic-settings`, `pyyaml`, `pypdf`, `playwright`, `httpx`, and `openai`; development dependencies `pytest` and `pytest-asyncio`. Ignore `.env`, personal profile/resume files, SQLite databases, Playwright state, and caches. Settings read secrets only from environment variables.
- [ ] **Step 4: Run `uv run pytest tests/test_settings.py -q`** and confirm defaults and environment overrides pass.
- [ ] **Step 5: Commit** as `chore: scaffold job application CLI`.

### Task 2: Load the explicit profile and resume evidence

**Files:**
- Create: `src/jobapply/profile.py`
- Create: `examples/profile.example.yaml`
- Test: `tests/test_profile.py`

**Interfaces:**
- Produces: Pydantic `Profile`, `ResumeFact`, and `EvidenceFact` models; `load_profile(path: Path) -> Profile`; `load_evidence(profile: Profile) -> list[EvidenceFact]`.
- A `Profile` contains a `resume_path`, contact fields, work history, education, skills, preferences, and an explicit `sensitive_answers` mapping. Each evidence fact has a stable ID, value, and source label.

- [ ] **Step 1: Write failing tests** for valid YAML, missing required keys, malformed YAML, PDF resume extraction, and absent sensitive answers.

```python
def test_missing_sensitive_answer_stays_missing(profile_yaml):
    profile = load_profile(profile_yaml)
    assert profile.sensitive_answers.get("work_authorization") is None
```

- [ ] **Step 2: Run `uv run pytest tests/test_profile.py -q`** and confirm failure before implementation.
- [ ] **Step 3: Implement schema validation and loading.** Use PyYAML safe loading and pypdf text extraction. Treat absent sensitive fields as absent; never fill them from resume inference. Reject paths that do not exist and malformed PDF/YAML with actionable errors. Add an example profile containing only fictional values.
- [ ] **Step 4: Run `uv run pytest tests/test_profile.py -q`** and confirm the profile and evidence tests pass.
- [ ] **Step 5: Commit** as `feat: load structured profile and resume evidence`.

### Task 3: Add application history and duplicate protection

**Files:**
- Create: `src/jobapply/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Produces: `ApplicationStatus` enum (`processing`, `submitted`, `deferred`, `failed`, `uncertain`); `HistoryStore(path: Path)` with `claim(url: str, retry_uncertain: bool = False) -> bool`, `finish(url: str, status: ApplicationStatus, details: dict) -> None`, and `get(url: str) -> dict | None`.
- URL identity removes fragments and known tracking parameters (`utm_*`, `lever-source`, `gh_src`) while preserving other query parameters that may identify the job.

- [ ] **Step 1: Write failing tests** for first claim, second claim, URL normalization, status persistence, and the distinction between submitted and uncertain. Verify an uncertain URL is refused by default and accepted only when `retry_uncertain=True`.

```python
def test_submitted_url_cannot_be_claimed_twice(history):
    assert history.claim("https://jobs.example/role/123?utm_source=x") is True
    history.finish("https://jobs.example/role/123", ApplicationStatus.submitted, {})
    assert history.claim("https://jobs.example/role/123") is False
```

- [ ] **Step 2: Run `uv run pytest tests/test_history.py -q`** and confirm the tests fail.
- [ ] **Step 3: Implement SQLite schema and transactions.** A claimed `processing` row prevents concurrent duplicate runs. A previous `submitted` row is always skipped. An `uncertain` row requires `claim(url, retry_uncertain=True)` from the explicit `--retry-uncertain` CLI option and must never be retried by default.
- [ ] **Step 4: Run `uv run pytest tests/test_history.py -q`** and confirm persistence and duplicate behavior pass.
- [ ] **Step 5: Commit** as `feat: track application outcomes locally`.

### Task 4: Define shared form types and deterministic field policy

**Files:**
- Create: `src/jobapply/forms.py`
- Create: `src/jobapply/mapping.py`
- Create: `src/jobapply/policy.py`
- Test: `tests/test_mapping.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces: `FormQuestion(id, label, required, kind, options, max_length)`; `FieldAnswer(question_id, value, evidence_ids, method)`; `map_known_question(question: FormQuestion, profile: Profile) -> FieldAnswer | None`; `submission_decision(answers, questions, profile) -> Decision` where `Decision` is `submit` or `defer` plus reason codes.
- `kind` is one of `text`, `email`, `phone`, `select`, `checkbox`, or `unknown`.

- [ ] **Step 1: Write failing tests** for exact alias mapping, select-option matching, unsupported question kinds, all missing required fields, and sensitive categories that lack an explicit profile value.

```python
def test_required_unknown_question_defers(profile):
    q = FormQuestion(id="q1", label="Anything else?", required=True,
                     kind="unknown", options=[], max_length=None)
    assert submission_decision([], [q], profile).action == "defer"
```

- [ ] **Step 2: Run `uv run pytest tests/test_mapping.py tests/test_policy.py -q`** and confirm failure.
- [ ] **Step 3: Implement exact mappings and policy.** Start with contact, location, links, work authorization, sponsorship, disability, veteran status, criminal history, and resume attachment fields. Sensitive categories are allowlisted and require explicit profile values. Unknown required fields and unmatched option values cause deferral. Never use a model to supply a personal value.
- [ ] **Step 4: Run `uv run pytest tests/test_mapping.py tests/test_policy.py -q`** and confirm deterministic mapping and policy tests pass.
- [ ] **Step 5: Commit** as `feat: map validated profile fields to application questions`.

### Task 5: Implement Jev as an evaluated structured-decision adapter

**Files:**
- Create: `src/jobapply/jev.py`
- Create: `scripts/jev_eval.py`
- Create: `tests/fixtures/jev/application_questions.jsonl`
- Test: `tests/test_jev.py`
- Test: `tests/test_jev_eval.py`

**Interfaces:**
- Produces: `JevClient.decide(state: dict, questions: dict) -> dict[str, DecisionAnswer]`; `DecisionAnswer` includes the selected value, probabilities when present, and model confidence when present.
- Choice values are constrained to available profile fact IDs, `free_text`, `sensitive_missing`, or `defer`; Jev cannot return an application answer value. Deterministic sensitive-field aliases take precedence over Jev, and `sensitive_missing` can only defer.

- [ ] **Step 1: Write failing tests** for valid typed responses, malformed responses, timeout/API errors, unknown selected fact IDs, and questions containing hostile instructions.

```python
def test_unknown_fact_id_becomes_defer(jev_client):
    jev_client.response = {"field": {"choice": "fact_not_in_state", "confidence": 0.99}}
    result = jev_client.decide(
        {"facts": ["email"]},
        {"field": {"type": "choice", "options": ["email", "defer"]}},
    )
    assert result["field"].value == "defer"
```

- [ ] **Step 2: Run `uv run pytest tests/test_jev.py -q`** and confirm the tests fail.
- [ ] **Step 3: Implement the HTTP adapter** against Jev's System One endpoint using httpx, configured API key and model, bounded timeout, and schema validation. Send only the question text and the minimum candidate field metadata needed; do not send a full resume when field names and descriptions suffice. Any API error or invalid answer becomes a defer result.
- [ ] **Step 4: Create a labeled fixture set** of at least 40 form questions across contact, employment, education, sensitive, free-text, and unknown categories. Include expected action and expected evidence field IDs. Add a mocked evaluation that reports accuracy, sensitive-question false-answer count, deferral rate, and model/rule disagreement; live network calls are opt-in and excluded from default tests.
- Configure `JEV_MIN_CONFIDENCE=0.98` as the initial experiment threshold. The evaluation must report errors at or above this value; if any sensitive question is answered without an explicit profile fact, Jev remains disabled for live routing until the policy/evaluation is corrected. Confidence is one gate among schema, evidence, and policy checks, never the sole submit condition.
- [ ] **Step 5: Run `uv run pytest tests/test_jev.py tests/test_jev_eval.py -q`** and confirm adapter and fixture evaluation pass.
- [ ] **Step 6: Add `scripts/jev_eval.py`** with an explicit `--live` flag. Without `--live`, reject execution before making network requests; with it, run Jev on the checked-in labeled fixture set, compare with exact rules, and report accuracy, confusion counts, confidence bins, defer rate, latency, sensitive-field false-answer count, and disagreement examples as JSON. Never include real profile or resume content in this harness.
- [ ] **Step 7: Commit** as `feat: add Jev structured decision adapter and evals`.

### Task 6: Generate evidence-referenced free-text answers

**Files:**
- Create: `src/jobapply/generation.py`
- Modify: `src/jobapply/policy.py`
- Modify: `src/jobapply/jev.py`
- Test: `tests/test_generation.py`
- Test: `tests/test_policy.py`
- Test: `tests/test_jev.py`

**Interfaces:**
- Produces: `SupportedClaim(text: str, evidence_ids: list[str])`; `DraftAnswer(claims: list[SupportedClaim])`; `TextGenerator.draft(question: FormQuestion, evidence: list[EvidenceFact], job_context: str) -> DraftAnswer`. Construct the final answer deterministically by joining claim text in order, so no unreferenced prose can bypass claim checks.
- OpenAI adapter uses the official Python SDK and Responses API. The model is configured by `OPENAI_MODEL`; no model name is hard-coded into application logic.
- Jev adds a separate typed `check_claim_support(...)` method returning `supported`, `unsupported`, or `defer` with confidence. Keep `decide()` restricted to profile-backed candidate IDs and routing controls; support checking must not expand application-answer choices.

- [ ] **Step 1: Write failing tests** for valid evidence references, unknown evidence IDs, blank text, maximum length, and failed generation requests.

```python
def test_draft_with_unknown_evidence_id_is_rejected(draft, evidence):
    draft.claims = [SupportedClaim(text="I led a team", evidence_ids=["missing-id"])]
    assert validate_draft(draft, evidence).action == "defer"
```

- [ ] **Step 2: Run `uv run pytest tests/test_generation.py -q`** and confirm the tests fail.
- [ ] **Step 3: Implement structured draft generation** that returns claim-level evidence IDs, using only relevant supplied facts and the job description. Validate schema, evidence IDs, required length limits, and factual-answer presence. Construct final text from the validated claims. Send only facts relevant to the question to the OpenAI API; do not send the complete profile or resume, and do not place sensitive profile fields into generation context unless the specific question requires the explicit value.
- [ ] **Step 4: Add a support-evaluation path.** Ask Jev whether each claim is supported by its cited evidence, as a typed yes/no decision. Require valid cited IDs and confidence of at least `JEV_MIN_CONFIDENCE=0.98`; missing, low-confidence, or negative support means defer. Log evaluation outcomes for comparison with labeled examples; do not treat this model check as formal proof or a standalone authorization to submit.
- [ ] **Step 5: Run `uv run pytest tests/test_generation.py tests/test_policy.py tests/test_jev.py -q`** using a fake Responses client and fake Jev client; confirm no tests require network access.
- [ ] **Step 6: Commit** as `feat: draft evidence-referenced application answers`.

### Task 7: Build Greenhouse and Lever browser adapters

**Files:**
- Create: `src/jobapply/ats/__init__.py`
- Create: `src/jobapply/ats/base.py`
- Create: `src/jobapply/ats/greenhouse.py`
- Create: `src/jobapply/ats/lever.py`
- Create: `tests/fixtures/ats/greenhouse.html`
- Create: `tests/fixtures/ats/lever.html`
- Test: `tests/test_ats_adapters.py`

**Interfaces:**
- Produces: `ATSAdapter` protocol with synchronous `matches(page) -> bool` and async `read_questions(page) -> list[FormQuestion]`, `read_job_context(page) -> str | None`, `fill(page, answers) -> None`, `submit(page) -> SubmissionResult`, and `confirm_submission(page) -> bool`.
- `SubmissionResult` is `confirmed`, `rejected`, or `uncertain` and includes a human-readable reason.

- [ ] **Step 1: Write failing browser tests** for adapter recognition, required fields, select controls, checkbox controls, file upload, validation errors, and confirmation state using local HTML fixtures.

```python
async def test_adapter_fill_does_not_submit(page, greenhouse_adapter):
    await page.set_content(
        '<form id="application-form"><label>Email'
        '<input name="email"></label><button type="submit">Apply</button></form>'
    )
    await greenhouse_adapter.fill(page, [FieldAnswer("email", "a@example.test", [], "profile")])
    assert await page.locator('input[name="email"]').input_value() == "a@example.test"
    assert await page.locator("#application-form").is_visible()
```

- [ ] **Step 2: Run `uv run pytest tests/test_ats_adapters.py -q`** and confirm failure before implementing adapters.
- [ ] **Step 3: Implement shared Playwright behavior** with an HTTPS-only host allowlist for Greenhouse and Lever, locator priority by accessible label/name, strict ambiguity detection, per-navigation timeout, and a separate submit call. Never click submit inside `fill`.
- [ ] **Step 4: Implement Greenhouse and Lever adapters** using the shared protocol. Unknown controls, multiple matching locators, CAPTCHA, login walls, unsupported custom questions, or absent confirmation produce `uncertain` or `deferred`; do not attempt bypasses.
- [ ] **Step 5: Install the local Chromium test browser** with `uv run playwright install chromium`.
- [ ] **Step 6: Run `uv run pytest tests/test_ats_adapters.py -q`** and confirm fixture tests pass without contacting external sites.
- [ ] **Step 7: Commit** as `feat: automate supported Greenhouse and Lever forms`.

### Task 8: Coordinate application processing and status transitions

**Files:**
- Create: `src/jobapply/workflow.py`
- Modify: `src/jobapply/ats/base.py`
- Modify: `src/jobapply/mapping.py`
- Test: `tests/test_workflow.py`
- Test: `tests/test_ats_adapters.py`
- Test: `tests/test_mapping.py`

**Interfaces:**
- Produces: async `ApplicationWorkflow.run(urls: list[str], dry_run: bool = False, retry_uncertain: bool = False) -> list[ApplicationOutcome]`.
- `ApplicationOutcome` includes normalized URL, status, employer/role when available, submitted timestamp, and reason.
- The ATS adapter exposes `verify_resume_upload(page, question_id) -> bool`; the workflow may resolve a required resume question only after this confirms a file is selected.
- Mapping exposes conservative `is_sensitive_question(question) -> bool` detection for sensitive/legal and protected-class question wording, in addition to exact sensitive aliases.

- [ ] **Step 1: Write failing orchestration tests** for supported ATS success, unknown ATS, missing required profile value, Jev defer, generation failure, duplicate, dry run, confirmed submit, and uncertain submit outcome.

```python
async def test_dry_run_never_calls_submit(workflow, adapter):
    await workflow.run([FIXTURE_URL], dry_run=True)
    assert adapter.submit_calls == 0
```

- [ ] **Step 2: Run `uv run pytest tests/test_workflow.py -q`** and confirm failure.
- [ ] **Step 3: Implement the state machine**: claim URL; open page; select ATS adapter; read questions and available job context; use deterministic mapper first; detect sensitive/legal questions before any Jev or generation call and defer unless an exact explicit sensitive profile mapping resolved them; call Jev only for unresolved structured mapping; generate only for free-text questions when job context is available; apply policy; fill; verify required resume upload before resolving it; stop at dry-run or submit; confirm and persist result. If job context is unavailable for a question that needs it, defer. Persist `uncertain` if the page loses state after submit or confirmation cannot be established. Ensure exceptions update history and do not create an implicit retry path. Refresh outcomes for duplicate URLs within one batch after the first claimed URL is processed.
- [ ] **Step 4: Run `uv run pytest tests/test_workflow.py -q`** with fake adapters and fake model clients; confirm each terminal status and duplicate handling pass.
- [ ] **Step 5: Commit** as `feat: coordinate application workflow with safe deferrals`.

### Task 9: Expose CLI commands and operational summaries

**Files:**
- Create: `src/jobapply/cli.py`
- Create: `tests/test_cli.py`
- Modify: `src/jobapply/history.py`
- Test: `tests/test_history.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `jobapply validate-profile`; `jobapply apply <urls-file> [--dry-run] [--retry-uncertain]`; `jobapply history [--status STATUS]`.
- URLs file is UTF-8 plain text, one URL per line; blank lines and lines beginning with `#` are ignored.
- `HistoryStore.list(status: ApplicationStatus | None = None) -> list[dict]` returns stored records, optionally filtered by status, for the history command.

- [ ] **Step 1: Write failing CliRunner tests** for usage/help, URL parsing, profile validation, dry run, per-job outcome summaries, and exit codes when one or more links defer or fail.

```python
def test_apply_dry_run_reports_without_submission(cli_runner, fake_workflow):
    result = cli_runner.invoke(app, ["apply", "jobs.txt", "--dry-run"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert fake_workflow.submit_calls == 0
```

- [ ] **Step 2: Run `uv run pytest tests/test_cli.py -q`** and confirm failure.
- [ ] **Step 3: Implement Typer commands.** `apply` performs autonomous submission by default when policy allows; `--dry-run` never submits. Print only concise per-job status and reason; do not print profile contents, resume text, prompts, or API keys. Use nonzero exit status when any application is deferred, failed, or uncertain.
- [ ] **Step 4: Add example profile documentation** to `examples/profile.example.yaml` with comments describing required profile keys and sensitive explicit-answer fields. Confirm `.gitignore` excludes any real profile, resume, `.env`, browser state, and history database.
- [ ] **Step 5: Run `uv run pytest tests/test_cli.py -q`** and confirm CLI behavior passes.
- [ ] **Step 6: Commit** as `feat: expose job application workflow through CLI`.

### Task 10: Verify end-to-end behavior with local ATS fixtures

**Files:**
- Create: `tests/test_end_to_end.py`
- Modify: `tests/fixtures/ats/greenhouse.html`
- Modify: `tests/fixtures/ats/lever.html`
- Modify: `src/jobapply/ats/base.py`
- Modify: `tests/test_ats_adapters.py`

**Interfaces:**
- Consumes: the CLI, workflow, SQLite history, local ATS adapters, fake Jev client, and fake text generator from prior tasks.
- Field identity matching normalizes case and surrounding/repeated whitespace across names, IDs, and accessible labels; if that produces multiple field matches, filling defers as ambiguous.

- [ ] **Step 1: Write end-to-end scenarios** for a complete profile-backed application, an application requiring a generated response, missing sensitive field deferral, duplicate rerun, unsupported question deferral, and unknown submit confirmation.

```python
async def test_second_run_skips_confirmed_application(local_app):
    first = await local_app.run([FIXTURE_URL])
    second = await local_app.run([FIXTURE_URL])
    assert first[0].status == "submitted"
    assert second[0].reason == "already_submitted"
    assert local_app.adapter.submit_calls == 1
```

- [ ] **Step 2: Run `uv run pytest tests/test_end_to_end.py -q`** and confirm the new scenarios fail before connecting components.
- [ ] **Step 3: Connect only the missing seams**; keep browser pages local and replace both remote model clients with deterministic fakes.
- [ ] **Step 4: Run `uv run pytest -q`** and confirm the complete default suite passes with zero network calls and zero real submissions.
- [ ] **Step 5: Commit** as `test: cover complete application workflow locally`.

## Spec Coverage Check

- Curated URL CLI, no discovery, and no UI: Tasks 8–9.
- Structured profile and resume source data: Task 2.
- Supported ATS subset: Task 7.
- Deterministic mapping and validated fields: Task 4.
- Jev experiment, labeled evaluation, and disagreement measurement: Task 5; support-check experiment in Task 6.
- Evidence-grounded free text: Task 6.
- Explicit sensitive fields and safe deferral: Tasks 2, 4, 6, and 8.
- Autonomous submission and confirmation outcomes: Tasks 7–9.
- Local audit history and repeat-run prevention: Tasks 3 and 8.
- No secrets or personal files committed: Tasks 1 and 9.
