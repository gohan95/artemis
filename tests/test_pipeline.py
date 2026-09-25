"""Tests for per-URL orchestration: fill, live-prompt for gaps, submit gate."""

from pathlib import Path

import pytest

from artemis.answers_store import LearnedAnswers
from artemis.ats.base import ATSAdapter, SubmissionResult
from artemis.drafting import Draft
from artemis.forms import FieldAnswer, FormQuestion
from artemis.history import ApplicationStatus, HistoryStore
from artemis.pipeline import ApplicationPipeline
from artemis.profile import Profile


class FakePage:
    url = "https://boards.greenhouse.io/acme/jobs/1"

    def __init__(self):
        self.closed = False
        self.events: list[str] = []

    async def close(self):
        self.closed = True
        self.events.append("closed")


class FakeAdapter(ATSAdapter):
    """A minimal stand-in that never touches a real browser."""

    def __init__(self, questions, host="boards.greenhouse.io", submit_status="confirmed"):
        self.questions = questions
        self.host = host
        self.filled: list[FieldAnswer] = []
        self.submitted = False
        self.submit_status = submit_status

    def supports_url(self, url: str) -> bool:
        return self.host in url

    def matches(self, page) -> bool:
        return self.host in page.url

    async def read_questions(self, page):
        return self.questions

    async def fill(self, page, answers):
        self.filled = list(answers)

    async def submit(self, page):
        self.submitted = True
        return SubmissionResult(self.submit_status, "clicked")


def make_profile(**overrides) -> Profile:
    defaults = dict(resume_path=Path(__file__), full_name="Riley Example", email="riley@example.test")
    defaults.update(overrides)
    return Profile(**defaults)


def make_pipeline(
    adapter, profile=None, learned=None, history=None, ask_user=None, tmp_path=None,
    on_filled=None, pages=None, draft_answer=None,
):
    def page_factory(url):
        page = FakePage()
        if pages is not None:
            pages.append(page)
        return page

    kwargs = {}
    if on_filled is not None:
        kwargs["on_filled"] = on_filled
    if draft_answer is not None:
        kwargs["draft_answer"] = draft_answer
    return ApplicationPipeline(
        profile=profile or make_profile(),
        history=history or HistoryStore(tmp_path / "history.sqlite3"),
        learned=learned or LearnedAnswers(tmp_path / "learned.yaml"),
        adapters=[adapter],
        page_factory=page_factory,
        ask_user=ask_user or (lambda q, draft=None: None),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_known_fields_are_filled_without_submitting_by_default(tmp_path: Path):
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert adapter.filled[0].value == "riley@example.test"
    assert adapter.submitted is False


@pytest.mark.asyncio
async def test_no_submit_without_explicit_flag_even_when_fully_resolved(tmp_path: Path):
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert adapter.submitted is False
    assert outcome.status != ApplicationStatus.submitted


@pytest.mark.asyncio
async def test_submits_when_flag_set_and_fully_resolved(tmp_path: Path):
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert adapter.submitted is True
    assert outcome.status == ApplicationStatus.submitted


@pytest.mark.asyncio
async def test_unresolved_gap_prompts_user_and_fills_answer(tmp_path: Path):
    questions = [FormQuestion(id="q1", label="Desired start date", required=False, kind="text")]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, ask_user=lambda q, draft=None: "Immediately", tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert adapter.filled[0].value == "Immediately"


@pytest.mark.asyncio
async def test_user_declines_to_answer_defers(tmp_path: Path):
    questions = [FormQuestion(id="q1", label="Why us?", required=True, kind="text")]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, ask_user=lambda q, draft=None: None, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert outcome.status == ApplicationStatus.deferred
    assert adapter.submitted is False
    assert "q1" in outcome.unresolved_fields


@pytest.mark.asyncio
async def test_sensitive_gap_is_never_prompted(tmp_path: Path):
    questions = [
        FormQuestion(
            id="work-auth", label="Are you authorized to work in the United States?",
            required=True, kind="select", options=["Yes", "No"],
        )
    ]
    adapter = FakeAdapter(questions)
    prompted: list[str] = []

    def ask(question, draft=None):
        prompted.append(question.id)
        return "Yes"

    pipeline = make_pipeline(adapter, ask_user=ask, tmp_path=tmp_path)
    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert prompted == []
    assert outcome.status == ApplicationStatus.deferred
    assert adapter.submitted is False


@pytest.mark.asyncio
async def test_sensitive_gap_blocks_even_when_page_marks_it_not_required(tmp_path: Path):
    """A sensitive question must never be treated as skippable just because
    the page's own HTML doesn't mark it required -- some ATS forms don't,
    even though skipping work authorization etc. is never acceptable."""
    questions = [
        FormQuestion(
            id="work-auth", label="Are you authorized to work in the United States?",
            required=False, kind="select", options=["Yes", "No"],
        )
    ]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert outcome.status == ApplicationStatus.deferred
    assert adapter.submitted is False


@pytest.mark.asyncio
async def test_draft_is_offered_and_accepted_via_ask_user(tmp_path: Path):
    questions = [FormQuestion(id="q1", label="Why us?", required=False, kind="text")]
    adapter = FakeAdapter(questions)
    draft = Draft(text="Drafted answer.", evidence_ids=("fact-1",))
    seen_drafts = []

    def ask(question, draft=None):
        seen_drafts.append(draft)
        return draft.text if draft else None

    pipeline = make_pipeline(
        adapter, ask_user=ask, draft_answer=lambda q: draft, tmp_path=tmp_path
    )

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert seen_drafts == [draft]
    assert adapter.filled[0].value == "Drafted answer."


@pytest.mark.asyncio
async def test_draft_can_be_declined_leaving_field_unresolved(tmp_path: Path):
    questions = [FormQuestion(id="q1", label="Why us?", required=False, kind="text")]
    adapter = FakeAdapter(questions)
    draft = Draft(text="Drafted answer.", evidence_ids=("fact-1",))

    pipeline = make_pipeline(
        adapter, ask_user=lambda q, draft=None: None, draft_answer=lambda q: draft, tmp_path=tmp_path
    )

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert "q1" in outcome.unresolved_fields


@pytest.mark.asyncio
async def test_grounded_drafter_llm_failure_still_completes_run_via_blank_prompt(tmp_path: Path):
    """GroundedDrafter is the guarantee that a drafting failure never fails a
    run -- it swallows LLMError and returns None (see test_drafting.py). This
    confirms that None flows through the pipeline exactly like "no drafter"."""
    from artemis.drafting import GroundedDrafter
    from artemis.llm import LLMError

    questions = [FormQuestion(id="q1", label="Why us?", required=False, kind="text")]
    adapter = FakeAdapter(questions)

    class _AlwaysFailsClient:
        def complete_json(self, prompt, schema):
            raise LLMError("boom")

    profile = make_profile()
    drafter = GroundedDrafter(_AlwaysFailsClient(), profile)
    pipeline = make_pipeline(
        adapter, profile=profile, ask_user=lambda q, draft=None: None,
        draft_answer=drafter, tmp_path=tmp_path,
    )

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert "q1" in outcome.unresolved_fields


@pytest.mark.asyncio
async def test_sensitive_question_is_never_passed_to_the_drafter(tmp_path: Path):
    questions = [
        FormQuestion(
            id="work-auth", label="Are you authorized to work in the United States?",
            required=True, kind="select", options=["Yes", "No"],
        )
    ]
    adapter = FakeAdapter(questions)
    drafted: list[str] = []

    def draft_answer(question):
        drafted.append(question.id)
        return None

    pipeline = make_pipeline(adapter, draft_answer=draft_answer, tmp_path=tmp_path)
    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert drafted == []
    assert outcome.status == ApplicationStatus.deferred


@pytest.mark.asyncio
async def test_unresolved_optional_field_does_not_block_the_run(tmp_path: Path):
    """Regression test: an unresolved OPTIONAL field must not defer the whole
    application. Only a required (or sensitive) gap may do that."""
    questions = [
        FormQuestion(id="email", label="Email Address", required=True, kind="email"),
        FormQuestion(id="cover_letter", label="Cover Letter", required=False, kind="text"),
    ]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, ask_user=lambda q, draft=None: None, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert outcome.status == ApplicationStatus.submitted
    assert adapter.submitted is True
    assert "cover_letter" in outcome.unresolved_fields
    assert any(a.question_id == "email" for a in adapter.filled)


@pytest.mark.asyncio
async def test_unresolved_optional_field_still_lands_on_filled_without_submit_flag(tmp_path: Path):
    questions = [
        FormQuestion(id="email", label="Email Address", required=True, kind="email"),
        FormQuestion(id="cover_letter", label="Cover Letter", required=False, kind="text"),
    ]
    adapter = FakeAdapter(questions)
    pipeline = make_pipeline(adapter, ask_user=lambda q, draft=None: None, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert adapter.submitted is False
    assert "cover_letter" in outcome.unresolved_fields


@pytest.mark.asyncio
async def test_on_filled_runs_before_the_page_closes(tmp_path: Path):
    """Regression test: a real usability bug where the browser page closed
    the instant its outcome was decided, before a person running headed
    without --submit could see anything filled. on_filled must be called
    with the still-open page, strictly before close()."""
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    pages: list[FakePage] = []
    calls: list[tuple] = []

    async def on_filled(page, outcome):
        calls.append((page.closed, outcome.status))
        page.events.append("on_filled")

    pipeline = make_pipeline(adapter, tmp_path=tmp_path, on_filled=on_filled, pages=pages)
    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=False)

    assert outcome.status == ApplicationStatus.filled
    assert calls == [(False, ApplicationStatus.filled)]  # page was NOT yet closed when called
    assert pages[0].events == ["on_filled", "closed"]
    assert pages[0].closed is True  # still closes afterward


@pytest.mark.asyncio
async def test_on_filled_does_not_run_for_an_early_defer(tmp_path: Path):
    """There is nothing to look at when the run defers before filling
    anything (unsupported ATS, sensitive gap, etc.) -- on_filled must not
    fire in that case."""
    adapter = FakeAdapter([], host="boards.greenhouse.io")
    calls: list[str] = []

    async def on_filled(page, outcome):
        calls.append(outcome.status)

    pipeline = make_pipeline(adapter, tmp_path=tmp_path, on_filled=on_filled)
    [outcome] = await pipeline.run(["https://jobs.example.com/1"], submit=False)

    assert outcome.status == ApplicationStatus.deferred
    assert calls == []


@pytest.mark.asyncio
async def test_on_filled_runs_for_submitted_outcome(tmp_path: Path):
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    calls: list[str] = []

    async def on_filled(page, outcome):
        calls.append(outcome.status)

    pipeline = make_pipeline(adapter, tmp_path=tmp_path, on_filled=on_filled)
    [outcome] = await pipeline.run(["https://boards.greenhouse.io/acme/jobs/1"], submit=True)

    assert outcome.status == ApplicationStatus.submitted
    assert calls == [ApplicationStatus.submitted]


@pytest.mark.asyncio
async def test_duplicate_run_is_skipped(tmp_path: Path):
    questions = [FormQuestion(id="email", label="Email Address", required=True, kind="email")]
    adapter = FakeAdapter(questions)
    history = HistoryStore(tmp_path / "history.sqlite3")
    pipeline = make_pipeline(adapter, history=history, tmp_path=tmp_path)

    url = "https://boards.greenhouse.io/acme/jobs/1"
    await pipeline.run([url], submit=True)
    [outcome] = await pipeline.run([url], submit=True)

    assert outcome.reason == "already processed"
    assert adapter.submitted is True  # only from the first run


@pytest.mark.asyncio
async def test_unsupported_host_defers(tmp_path: Path):
    adapter = FakeAdapter([], host="boards.greenhouse.io")
    pipeline = make_pipeline(adapter, tmp_path=tmp_path)

    [outcome] = await pipeline.run(["https://jobs.example.com/1"], submit=False)

    assert outcome.status == ApplicationStatus.deferred
    assert outcome.reason == "unsupported ATS"
