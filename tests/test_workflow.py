from pathlib import Path
from types import SimpleNamespace

import pytest

from jobapply.forms import FormQuestion
from jobapply.generation import DraftAnswer, SupportedClaim
from jobapply.history import ApplicationStatus, HistoryStore
from jobapply.jev import ClaimSupport, DecisionAnswer
from jobapply.profile import EvidenceFact, Profile
from jobapply.settings import Settings
from jobapply.workflow import ApplicationWorkflow


URL = "https://boards.greenhouse.io/example/jobs/123?utm_source=fixture"


class FakePage:
    def __init__(self, url=URL):
        self.url = url
        self.closed = False

    async def goto(self, url):
        self.url = url

    async def close(self):
        self.closed = True


class FakeAdapter:
    def __init__(self, questions=(), context="Build useful software", submit_result=None):
        self.questions = list(questions)
        self.context = context
        self.submit_result = submit_result or SimpleNamespace(status="confirmed", reason="Application received")
        self.fill_calls = []
        self.submit_calls = 0
        self.uploaded = False
        self.resume_verified = True

    def matches(self, page):
        return page.url.startswith("https://boards.greenhouse.io/")

    async def read_questions(self, page):
        return self.questions

    async def read_job_context(self, page):
        return self.context

    async def fill(self, page, answers):
        self.fill_calls.append(list(answers))
        self.uploaded = any(answer.question_id == "resume" for answer in answers)

    async def verify_resume_upload(self, page, question_id):
        return self.resume_verified and self.uploaded and question_id == "resume"

    async def submit(self, page):
        self.submit_calls += 1
        return self.submit_result

    async def confirm_submission(self, page):
        return self.submit_result.status == "confirmed"


class FakeJev:
    settings = Settings(jev_min_confidence=0.98)

    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return self.decisions

    def check_claim_support(self, question, claim, evidence):
        return ClaimSupport("supported", 0.99, [fact["id"] for fact in evidence])


class FakeGenerator:
    def __init__(self, draft=None, error=None):
        self.draft_result = draft or DraftAnswer(
            claims=[SupportedClaim(text="I led a team of eight engineers.", evidence_ids=["work.team"])]
        )
        self.error = error
        self.calls = []

    def draft(self, question, evidence, job_context):
        self.calls.append((question, evidence, job_context))
        if self.error:
            raise self.error
        return self.draft_result


@pytest.fixture
def profile(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    return Profile(resume_path=resume, full_name="Ada Example", email="ada@example.test")


@pytest.fixture
def evidence():
    return [
        EvidenceFact(id="profile.full_name", value="Ada Example", source="profile"),
        EvidenceFact(id="profile.email", value="ada@example.test", source="profile"),
        EvidenceFact(id="work.team", value="Led a team of eight engineers", source="profile"),
    ]


def make_workflow(tmp_path, profile, evidence, adapter, *, jev=None, generator=None, adapters=None):
    return ApplicationWorkflow(
        profile=profile,
        evidence=evidence,
        history=HistoryStore(tmp_path / "history.sqlite3"),
        adapters=adapters if adapters is not None else [adapter],
        page_factory=lambda url: FakePage(url),
        jev_client=jev or FakeJev(),
        generator=generator or FakeGenerator(),
        settings=Settings(),
    )


def standard_questions():
    return [
        FormQuestion("name", "Full Name", True, "text", [], None),
        FormQuestion("email", "Email Address", True, "email", [], None),
    ]


async def test_supported_ats_maps_fills_and_records_confirmed_submission(tmp_path, profile, evidence):
    adapter = FakeAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcomes = await workflow.run([URL])

    assert outcomes[0].url == "https://boards.greenhouse.io/example/jobs/123"
    assert outcomes[0].status == ApplicationStatus.submitted
    assert outcomes[0].submitted_at is not None
    assert adapter.submit_calls == 1
    assert workflow.history.get(URL)["status"] == "submitted"


async def test_unknown_ats_is_deferred_without_submission(tmp_path, profile, evidence):
    adapter = FakeAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter, adapters=[])

    outcome = (await workflow.run(["https://unknown.example/jobs/1"]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert "unsupported" in outcome.reason
    assert adapter.submit_calls == 0


async def test_adapter_context_error_is_persisted_as_deferred(tmp_path, profile, evidence):
    class BrokenAdapter(FakeAdapter):
        async def read_questions(self, page):
            raise RuntimeError("page state unavailable")

    adapter = BrokenAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert "page state unavailable" in outcome.reason
    assert workflow.history.get(URL)["status"] == "deferred"


async def test_missing_required_profile_value_defers_before_fill(tmp_path, profile, evidence):
    profile.email = None
    adapter = FakeAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert "missing_required_answer" in outcome.reason
    assert adapter.fill_calls == []


async def test_jev_defer_prevents_fill_and_submit(tmp_path, profile, evidence):
    question = FormQuestion("q", "Work arrangement", True, "select", ["Remote", "Hybrid"], None)
    jev = FakeJev({"q": DecisionAnswer("defer")})
    adapter = FakeAdapter([*standard_questions(), question])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, jev=jev)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert len(jev.calls) == 1
    assert adapter.fill_calls == []
    assert adapter.submit_calls == 0


async def test_free_text_generation_failure_defers_without_submission(tmp_path, profile, evidence):
    question = FormQuestion("q", "Describe your team leadership", True, "text", [], 200)
    generator = FakeGenerator(error=RuntimeError("model unavailable"))
    adapter = FakeAdapter([*standard_questions(), question])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, generator=generator)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert adapter.fill_calls == []
    assert adapter.submit_calls == 0


async def test_validated_free_text_answer_can_resolve_its_required_question(tmp_path, profile, evidence):
    question = FormQuestion("q", "Describe your team leadership", True, "text", [], 200)
    generator = FakeGenerator()
    adapter = FakeAdapter([*standard_questions(), question])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, generator=generator)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.submitted
    assert any(
        answer.question_id == "q" and answer.value == "I led a team of eight engineers."
        for answer in adapter.fill_calls[0]
    )
    assert adapter.submit_calls == 1


async def test_duplicate_url_is_not_opened_or_submitted(tmp_path, profile, evidence):
    adapter = FakeAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter)
    await workflow.run([URL])
    adapter.submit_calls = 0
    adapter.fill_calls.clear()

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.submitted
    assert outcome.reason == "already processed"
    assert adapter.submit_calls == 0
    assert adapter.fill_calls == []


async def test_dry_run_fills_but_never_submits(tmp_path, profile, evidence):
    adapter = FakeAdapter(standard_questions())
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcome = (await workflow.run([URL], dry_run=True))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert "dry run" in outcome.reason
    assert adapter.fill_calls
    assert adapter.submit_calls == 0
    assert workflow.history.get(URL)["status"] == "deferred"


async def test_uncertain_submit_is_persisted_and_not_implicitly_retried(tmp_path, profile, evidence):
    adapter = FakeAdapter(standard_questions(), submit_result=SimpleNamespace(status="uncertain", reason="page lost"))
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    first = (await workflow.run([URL]))[0]
    second = (await workflow.run([URL]))[0]

    assert first.status == ApplicationStatus.uncertain
    assert second.status == ApplicationStatus.uncertain
    assert second.reason == "already processed"
    assert adapter.submit_calls == 1
    assert workflow.history.get(URL)["status"] == "uncertain"


async def test_unknown_structured_choice_calls_jev_with_fact_identifiers(tmp_path, profile, evidence):
    choice = FormQuestion("work", "How did you hear about us?", True, "select", ["Referral", "Other"], None)
    jev = FakeJev({"work": DecisionAnswer("free_text")})
    adapter = FakeAdapter([*standard_questions(), choice])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, jev=jev)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert jev.calls[0][0]["facts"] == []
    assert jev.calls[0][1]["work"]["options"] == ["free_text", "defer"]
    assert adapter.submit_calls == 0


async def test_required_resume_is_removed_from_gate_only_after_fill(tmp_path, profile, evidence):
    resume = FormQuestion("resume", "Resume", True, "unknown", [], None)
    adapter = FakeAdapter([*standard_questions(), resume])
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.submitted
    answers = adapter.fill_calls[0]
    assert any(answer.question_id == "resume" and answer.value == str(profile.resume_path) for answer in answers)
    assert adapter.submit_calls == 1


async def test_required_resume_without_verified_upload_defers(tmp_path, profile, evidence):
    resume = FormQuestion("resume", "Resume", True, "unknown", [], None)
    adapter = FakeAdapter([*standard_questions(), resume])
    adapter.resume_verified = False
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert "resume" in outcome.reason
    assert adapter.submit_calls == 0


async def test_missing_sensitive_value_is_never_sent_to_jev(tmp_path, profile, evidence):
    sensitive = FormQuestion(
        "sponsor", "Do you require sponsorship?", True, "select", ["Yes", "No"], None
    )
    jev = FakeJev({"sponsor": DecisionAnswer("free_text")})
    adapter = FakeAdapter([*standard_questions(), sensitive])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, jev=jev)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert jev.calls == []
    assert adapter.submit_calls == 0


@pytest.mark.parametrize(
    "label",
    [
        "Will you need an employer to sponsor your visa?",
        "Have you ever received a criminal conviction?",
    ],
)
async def test_alternate_sensitive_free_text_defers_before_generation(tmp_path, profile, evidence, label):
    question = FormQuestion("sensitive", label, True, "text", [], 200)
    jev = FakeJev()
    generator = FakeGenerator()
    adapter = FakeAdapter([*standard_questions(), question])
    workflow = make_workflow(tmp_path, profile, evidence, adapter, jev=jev, generator=generator)

    outcome = (await workflow.run([URL]))[0]

    assert outcome.status == ApplicationStatus.deferred
    assert generator.calls == []
    assert jev.calls == []
    assert adapter.fill_calls == []
    assert adapter.submit_calls == 0


async def test_production_batch_duplicate_reports_completed_first_result(
    tmp_path, profile, evidence, monkeypatch
):
    from types import ModuleType
    import sys

    class Browser:
        async def new_page(self):
            class NavigablePage(FakePage):
                async def goto(self, url, wait_until=None):
                    self.url = url
            return NavigablePage()

        async def close(self):
            pass

    class Chromium:
        async def launch(self, headless=True):
            return Browser()

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            pass

    async def start():
        return Playwright()

    async_playwright_module = ModuleType("playwright.async_api")
    async_playwright_module.async_playwright = lambda: SimpleNamespace(start=start)
    playwright_module = ModuleType("playwright")
    playwright_module.async_api = async_playwright_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_playwright_module)

    adapter = FakeAdapter(standard_questions())
    workflow = ApplicationWorkflow(
        profile=profile,
        evidence=evidence,
        history=HistoryStore(tmp_path / "history.sqlite3"),
        adapters=[adapter],
        jev_client=FakeJev(),
        generator=FakeGenerator(),
        settings=Settings(),
    )

    outcomes = await workflow.run([
        "https://boards.greenhouse.io/example/jobs/123?utm_source=one",
        "https://boards.greenhouse.io/example/jobs/123?utm_medium=two",
    ])

    assert [outcome.status for outcome in outcomes] == [
        ApplicationStatus.submitted,
        ApplicationStatus.submitted,
    ]
    assert outcomes[1].reason == "already processed"
    assert adapter.submit_calls == 1


async def test_browser_startup_failure_refreshes_same_batch_duplicate(
    tmp_path, profile, evidence, monkeypatch
):
    from types import ModuleType
    import sys

    async def start():
        raise RuntimeError("browser unavailable")

    async_playwright_module = ModuleType("playwright.async_api")
    async_playwright_module.async_playwright = lambda: SimpleNamespace(start=start)
    playwright_module = ModuleType("playwright")
    playwright_module.async_api = async_playwright_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_playwright_module)
    workflow = ApplicationWorkflow(
        profile=profile,
        evidence=evidence,
        history=HistoryStore(tmp_path / "history.sqlite3"),
        adapters=[],
        jev_client=FakeJev(),
        generator=FakeGenerator(),
        settings=Settings(),
    )

    outcomes = await workflow.run([
        "https://boards.greenhouse.io/example/jobs/123?utm_source=one",
        "https://boards.greenhouse.io/example/jobs/123?utm_medium=two",
    ])

    assert [outcome.status for outcome in outcomes] == [
        ApplicationStatus.deferred,
        ApplicationStatus.deferred,
    ]
    assert outcomes[1].reason == "already processed"


async def test_uncertain_submit_retries_only_when_explicitly_requested(tmp_path, profile, evidence):
    adapter = FakeAdapter(
        standard_questions(),
        submit_result=SimpleNamespace(status="uncertain", reason="page lost"),
    )
    workflow = make_workflow(tmp_path, profile, evidence, adapter)

    await workflow.run([URL])
    outcome = (await workflow.run([URL], retry_uncertain=True))[0]

    assert outcome.status == ApplicationStatus.uncertain
    assert adapter.submit_calls == 2
