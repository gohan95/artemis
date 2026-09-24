from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import Error, async_playwright

from jobapply.ats.base import AdapterDeferred, SubmissionResult
from jobapply.ats.greenhouse import GreenhouseAdapter
from jobapply.ats.lever import LeverAdapter
from jobapply.forms import FieldAnswer


FIXTURES = Path(__file__).parent / "fixtures" / "ats"


@pytest.fixture
async def browser_instance():
    async with async_playwright() as playwright:
        system_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        try:
            browser = await playwright.chromium.launch(
                executable_path=str(system_chrome) if system_chrome.exists() else None
            )
        except Error as error:
            pytest.skip(f"local browser could not start: {error}")
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser_instance, request):
    page = await browser_instance.new_page()
    host = getattr(request, "param", "boards.greenhouse.io")
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    yield page
    await page.close()


@pytest.mark.parametrize(
    ("adapter", "host"),
    [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")],
)
def test_adapter_recognizes_only_its_allowlisted_https_host(adapter, host):
    assert adapter.matches(SimpleNamespace(url=f"https://{host}/jobs/123"))
    assert not adapter.matches(SimpleNamespace(url=f"http://{host}/jobs/123"))
    assert not adapter.matches(SimpleNamespace(url="https://boards.greenhouse.io.attacker.test/jobs/1"))


@pytest.mark.parametrize("adapter, host", [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")])
async def test_read_questions_reports_required_and_supported_control_kinds(adapter, host, browser_instance):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    questions = await adapter.read_questions(page)
    by_label = {question.label: question for question in questions}
    assert by_label["Email"].required is True
    assert by_label["Email"].kind == "email"
    assert any(question.kind == "select" for question in questions)
    assert any(question.kind == "checkbox" for question in questions)
    assert by_label["Resume"].kind == "unknown"
    await page.close()


@pytest.mark.parametrize("adapter, host", [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")])
async def test_read_job_context_uses_local_job_description(adapter, host, browser_instance):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    assert await adapter.read_job_context(page) == (
        "Build useful software with a kind team."
        if host.endswith("greenhouse.io")
        else "Help customers solve hard problems."
    )
    await page.close()


@pytest.mark.parametrize("adapter, host", [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")])
async def test_fill_supports_text_select_checkbox_and_local_file_without_submitting(adapter, host, browser_instance, tmp_path):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-local-test")
    answers = [
        FieldAnswer("name", "Ada Example", [], "profile"),
        FieldAnswer("email", "ada@example.test", [], "profile"),
        FieldAnswer("authorization", "Yes", [], "profile"),
        FieldAnswer("remote", "yes", [], "profile"),
        FieldAnswer("resume", str(resume), [], "profile"),
    ]
    await adapter.fill(page, answers)
    assert await page.locator("[type=email]").input_value() == "ada@example.test"
    assert await page.locator("select").input_value() == "Yes"
    assert await page.locator("[type=checkbox]").is_checked()
    assert await page.locator("[type=file]").input_value() != ""
    assert await page.locator("[role=alert]").is_hidden()
    assert await page.locator("form").is_visible()
    await page.close()


async def test_fill_rejects_ambiguous_matching_fields(browser_instance):
    page = await browser_instance.new_page()
    await page.route("https://boards.greenhouse.io/**", lambda route: route.fulfill(
        body='<form><label>Email <input name="email"></label><label>Email <input name="email2"></label></form>',
        content_type="text/html",
    ))
    await page.goto("https://boards.greenhouse.io/jobs/ambiguous")
    with pytest.raises(AdapterDeferred, match="ambiguous"):
        await GreenhouseAdapter().fill(page, [FieldAnswer("email", "ada@example.test", [], "profile")])
    await page.close()


@pytest.mark.parametrize("adapter, host", [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")])
async def test_submit_is_explicit_and_validation_error_is_rejected(adapter, host, browser_instance):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    result = await adapter.submit(page)
    assert isinstance(result, SubmissionResult)
    assert result.status == "rejected"
    assert "required" in result.reason.lower()
    await page.close()


async def test_confirmation_requires_an_explicit_success_state(browser_instance):
    page = await browser_instance.new_page()
    await page.route("https://boards.greenhouse.io/**", lambda route: route.fulfill(path=FIXTURES / "greenhouse.html"))
    await page.goto("https://boards.greenhouse.io/jobs/123")
    adapter = GreenhouseAdapter()
    assert await adapter.confirm_submission(page) is False
    await page.locator("#confirmation").evaluate("element => element.hidden = false")
    assert await adapter.confirm_submission(page) is True
    await page.close()


async def test_submit_refuses_off_allowlist_formaction(browser_instance):
    page = await browser_instance.new_page()
    await page.route("**/*", lambda route: route.abort())
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><button type="submit" formaction="https://evil.example/apply">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/unsafe-action")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert "allowlist" in result.reason
    await page.close()


async def test_access_wall_defers_without_attempting_bypass(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<main><h1>Log in to continue</h1><div class="g-recaptcha"></div></main>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/login-wall")
    with pytest.raises(AdapterDeferred, match="login|CAPTCHA"):
        await GreenhouseAdapter().read_questions(page)
    await page.close()


async def test_custom_question_widgets_defer_without_interaction(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><div role="combobox" aria-label="Custom question"></div></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/custom-question")
    with pytest.raises(AdapterDeferred, match="custom question"):
        await GreenhouseAdapter().read_questions(page)
    await page.close()
