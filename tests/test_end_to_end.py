"""CLI-to-browser application scenarios backed only by local ATS fixtures."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from playwright.sync_api import Error as SyncPlaywrightError, sync_playwright
from typer.testing import CliRunner

from jobapply import cli
from jobapply.ats.greenhouse import GreenhouseAdapter
from jobapply.ats.lever import LeverAdapter
from jobapply.generation import DraftAnswer, SupportedClaim
from jobapply.history import ApplicationStatus, HistoryStore
from jobapply.jev import ClaimSupport, DecisionAnswer
from jobapply.profile import EvidenceFact, Profile
from jobapply.settings import Settings
from jobapply.workflow import ApplicationWorkflow


FIXTURES = Path(__file__).parent / "fixtures" / "ats"
SYSTEM_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
GREENHOUSE_URL = "https://boards.greenhouse.io/example/jobs/123"
LEVER_URL = "https://jobs.lever.co/example/123"


@pytest.fixture(scope="module", autouse=True)
def local_browser_available():
    """Skip browser scenarios with the concrete local runtime failure reason."""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(SYSTEM_CHROME) if SYSTEM_CHROME.exists() else None,
                headless=True,
            )
            browser.close()
    except (SyncPlaywrightError, OSError) as error:
        pytest.skip(f"local browser could not start: {error}")


class DeterministicJev:
    settings = Settings(jev_min_confidence=0.98)

    def __init__(self):
        self.decisions = []
        self.support_checks = []

    def decide(self, state, questions):
        self.decisions.append((state, questions))
        return {question_id: DecisionAnswer("defer") for question_id in questions}

    def check_claim_support(self, question, claim, evidence):
        self.support_checks.append((question, claim, evidence))
        return ClaimSupport("supported", 0.99, [fact["id"] for fact in evidence])


class DeterministicGenerator:
    def __init__(self):
        self.questions = []

    def draft(self, question, evidence, job_context):
        self.questions.append(question.label)
        return DraftAnswer(claims=[SupportedClaim(
            text="I led a team of eight engineers.", evidence_ids=["work.team"]
        )])


class CountingAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.submit_calls = 0
        self.filled_answers = []

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def submit(self, page):
        self.submit_calls += 1
        return await self.adapter.submit(page)

    async def fill(self, page, answers):
        self.filled_answers.append(list(answers))
        await self.adapter.fill(page, answers)


class FixturePage:
    """Delegate the actual Playwright page and close its per-run browser."""

    def __init__(self, page, browser, playwright):
        self._page = page
        self._browser = browser
        self._playwright = playwright

    def __getattr__(self, name):
        return getattr(self._page, name)

    async def close(self):
        await self._page.close()
        await self._browser.close()
        await self._playwright.stop()


class FixturePages:
    def __init__(self):
        self.outbound_requests = []
        self.requests = []

    async def __call__(self, url):
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            executable_path=str(SYSTEM_CHROME) if SYSTEM_CHROME.exists() else None,
            headless=True,
        )
        page = await browser.new_page()

        async def serve_fixture(route):
            request_url = route.request.url
            hostname = request_url.split("/", 3)[2]
            self.requests.append((route.request.method, route.request.resource_type, request_url))
            fixture_name = "lever.html" if hostname == "jobs.lever.co" else "greenhouse.html"
            if hostname not in {"boards.greenhouse.io", "jobs.lever.co"}:
                self.outbound_requests.append(request_url)
                await route.abort()
                return
            await route.fulfill(path=FIXTURES / fixture_name)

        await page.route("**/*", serve_fixture)
        return FixturePage(page, browser, playwright)


def assert_only_local_fixture_navigation(local, url, scenario):
    expected_url = f"{url}?scenario={scenario}"
    assert local.pages.outbound_requests == []
    assert local.pages.requests == [("GET", "document", expected_url)]


class LocalApplication:
    def __init__(self, tmp_path, *, sensitive=True):
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"local fixture resume")
        self.profile = Profile(
            resume_path=resume,
            full_name="Ada Example",
            email="ada@example.test",
            sensitive_answers={"work_authorization": "Yes"} if sensitive else {},
        )
        self.evidence = [
            EvidenceFact(id="profile.full_name", value="Ada Example", source="profile"),
            EvidenceFact(id="profile.email", value="ada@example.test", source="profile"),
            EvidenceFact(
                id="profile.sensitive_answers.work_authorization", value="Yes", source="profile"
            ),
            EvidenceFact(id="work.team", value="Led a team of eight engineers", source="profile"),
        ]
        self.history = HistoryStore(tmp_path / "history.sqlite3")
        self.runner = CliRunner()
        self.adapters = [CountingAdapter(GreenhouseAdapter()), CountingAdapter(LeverAdapter())]
        self.pages = FixturePages()
        self.jev = DeterministicJev()
        self.generator = DeterministicGenerator()

    def workflow(self):
        return ApplicationWorkflow(
            profile=self.profile,
            evidence=self.evidence,
            history=self.history,
            adapters=self.adapters,
            page_factory=self.pages,
            jev_client=self.jev,
            generator=self.generator,
            settings=Settings(),
        )

    def run(self, monkeypatch, tmp_path, url, scenario):
        urls_file = tmp_path / "curated-jobs.txt"
        urls_file.write_text(f"{url}?scenario={scenario}\n", encoding="utf-8")
        monkeypatch.setattr(cli, "_workflow", self.workflow)
        result = self.runner.invoke(cli.app, ["apply", str(urls_file)])
        return result


def test_cli_submits_profile_backed_application_and_persists_confirmation(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path)

    result = local.run(monkeypatch, tmp_path, GREENHOUSE_URL, "profile")

    assert result.exit_code == 0, (
        result.output, local.history.get(f"{GREENHOUSE_URL}?scenario=profile")
    )
    assert "job 1: submitted" in result.output
    assert local.history.get(f"{GREENHOUSE_URL}?scenario=profile")["status"] == ApplicationStatus.submitted
    assert local.adapters[0].submit_calls == 1
    assert_only_local_fixture_navigation(local, GREENHOUSE_URL, "profile")


def test_cli_submits_supported_generated_answer_from_local_fixture(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path)

    result = local.run(monkeypatch, tmp_path, LEVER_URL, "generated")

    assert result.exit_code == 0, result.output
    assert local.history.get(f"{LEVER_URL}?scenario=generated")["status"] == ApplicationStatus.submitted
    assert local.adapters[1].submit_calls == 1
    assert any(
        answer.question_id == "leadership"
        and answer.value == "I led a team of eight engineers."
        and answer.method == "generated"
        for answer in local.adapters[1].filled_answers[0]
    )
    assert (
        "Describe your team leadership",
        "I led a team of eight engineers.",
        [{"id": "work.team", "value": "Led a team of eight engineers"}],
    ) in local.jev.support_checks
    assert_only_local_fixture_navigation(local, LEVER_URL, "generated")


def test_cli_defers_required_sensitive_question_when_profile_has_no_answer(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path, sensitive=False)

    result = local.run(monkeypatch, tmp_path, GREENHOUSE_URL, "missing-sensitive")

    assert result.exit_code != 0
    assert local.history.get(f"{GREENHOUSE_URL}?scenario=missing-sensitive")["status"] == ApplicationStatus.deferred
    assert local.adapters[0].submit_calls == 0
    assert local.jev.decisions == []
    assert local.generator.questions == []
    assert_only_local_fixture_navigation(local, GREENHOUSE_URL, "missing-sensitive")


def test_cli_duplicate_rerun_skips_confirmed_application(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path)

    first = local.run(monkeypatch, tmp_path, GREENHOUSE_URL, "profile")
    second = local.run(monkeypatch, tmp_path, GREENHOUSE_URL, "profile")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "job 1: submitted" in second.output
    assert local.adapters[0].submit_calls == 1
    assert_only_local_fixture_navigation(local, GREENHOUSE_URL, "profile")


def test_cli_defers_unknown_required_question_without_submission(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path)

    result = local.run(monkeypatch, tmp_path, LEVER_URL, "unsupported")

    assert result.exit_code != 0
    assert local.history.get(f"{LEVER_URL}?scenario=unsupported")["status"] == ApplicationStatus.deferred
    assert local.adapters[1].submit_calls == 0
    assert local.jev.decisions == []
    assert_only_local_fixture_navigation(local, LEVER_URL, "unsupported")


def test_cli_marks_submit_without_observable_confirmation_uncertain(tmp_path, monkeypatch):
    local = LocalApplication(tmp_path)

    result = local.run(monkeypatch, tmp_path, GREENHOUSE_URL, "unknown-confirmation")

    assert result.exit_code != 0
    record = local.history.get(f"{GREENHOUSE_URL}?scenario=unknown-confirmation")
    assert record["status"] == ApplicationStatus.uncertain
    assert record["details"]["reason"] == "submission confirmation was not observed"
    assert local.adapters[0].submit_calls == 1
    assert_only_local_fixture_navigation(local, GREENHOUSE_URL, "unknown-confirmation")
