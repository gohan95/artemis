from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import Error, async_playwright

from jobapply.ats.base import AdapterDeferred, BaseATSAdapter, SubmissionResult
from jobapply.ats.greenhouse import GreenhouseAdapter
from jobapply.ats.lever import LeverAdapter
from jobapply.forms import FieldAnswer, FormQuestion


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


@pytest.mark.parametrize(
    ("adapter", "accepted", "rejected"),
    [
        (GreenhouseAdapter(), ("boards.greenhouse.io", "job-boards.greenhouse.io"), ("evil.greenhouse.io", "boards.greenhouse.com")),
        (LeverAdapter(), ("jobs.lever.co",), ("evil.lever.co", "jobs.lever.com")),
    ],
)
def test_adapter_matches_only_fixture_backed_board_hosts(adapter, accepted, rejected):
    for host in accepted:
        assert adapter.matches(SimpleNamespace(url=f"https://{host}/jobs/123"))
    for host in rejected:
        assert not adapter.matches(SimpleNamespace(url=f"https://{host}/jobs/123"))


def test_required_control_preflight_rejects_unknown_or_unanswered_values():
    controls = [
        {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "filled", "checked": False, "kind": "text"},
        {"required": True, "supported": False, "valid": True, "file": False, "fileSelected": False, "value": "", "checked": False, "kind": "unknown"},
    ]
    assert not BaseATSAdapter._required_controls_are_ready(controls)
    controls[1]["supported"] = True
    controls[0]["valid"] = False
    assert not BaseATSAdapter._required_controls_are_ready(controls)


def test_required_file_control_is_ready_only_when_a_file_is_selected():
    control = {"required": True, "supported": True, "valid": True, "file": True, "fileSelected": False, "value": "", "checked": False, "kind": "file"}
    assert not BaseATSAdapter._required_controls_are_ready([control])
    control["fileSelected"] = True
    assert BaseATSAdapter._required_controls_are_ready([control])


@pytest.mark.parametrize(
    "control",
    [
        {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "", "checked": False, "kind": "text"},
        {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "", "checked": False, "kind": "checkbox"},
        {"required": True, "supported": False, "valid": False, "file": False, "fileSelected": False, "value": "", "checked": False, "kind": "unknown"},
    ],
)
def test_required_control_readiness_requires_real_values_and_known_state(control):
    assert not BaseATSAdapter._required_controls_are_ready([control])


def test_required_checkbox_readiness_uses_checked_state():
    control = {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "", "checked": False, "kind": "checkbox"}
    assert not BaseATSAdapter._required_controls_are_ready([control])
    control["checked"] = True
    assert BaseATSAdapter._required_controls_are_ready([control])


def test_required_unknown_question_is_unready_unless_it_is_a_selected_file():
    unknown = {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "answer", "checked": False, "kind": "unknown"}
    assert not BaseATSAdapter._required_controls_are_ready([unknown])

    selected_file = {"required": True, "supported": True, "valid": True, "file": True, "fileSelected": True, "value": "", "checked": False, "kind": "file"}
    assert BaseATSAdapter._required_controls_are_ready([selected_file])


def test_required_multi_select_is_unready_even_with_a_selected_value():
    control = {"required": True, "supported": True, "valid": True, "file": False, "fileSelected": False, "value": "option-a", "checked": False, "kind": "select", "multiple": True}
    assert not BaseATSAdapter._required_controls_are_ready([control])


def test_submit_preflight_requires_selected_file_for_extracted_unknown_question():
    question = FormQuestion(
        id="resume", label="Resume", required=True, kind="unknown", options=[], max_length=None
    )
    selected_file = {"id": "resume", "file": True, "fileSelected": True}
    unselected_file = {"id": "resume", "file": True, "fileSelected": False}
    assert BaseATSAdapter._required_unknown_questions_are_ready([question], [selected_file])
    assert not BaseATSAdapter._required_unknown_questions_are_ready([question], [unselected_file])


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


@pytest.mark.parametrize(
    "adapter, host",
    [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")],
)
async def test_resume_upload_verification_requires_selected_file(adapter, host, browser_instance, tmp_path):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-local-test")

    assert await adapter.verify_resume_upload(page, "resume") is False
    await adapter.fill(page, [FieldAnswer("resume", str(resume), [], "profile")])
    assert await adapter.verify_resume_upload(page, "resume") is True
    assert await adapter.verify_resume_upload(page, "not-resume") is False
    await page.locator("[type=file]").set_input_files([])
    assert await adapter.verify_resume_upload(page, "resume") is False
    await page.close()


@pytest.mark.parametrize(
    "other_control",
    [
        '<input name="  EMAIL  ">',
        '<input id="  EMAIL  " name="email2">',
        '<label>  EMAIL   <input name="email2"></label>',
        '<input name="email2" aria-label="  EMAIL  ">',
        '<span id="email-label">  EMAIL  </span><input name="email2" aria-labelledby="email-label">',
    ],
    ids=["name", "id", "associated-label", "aria-label", "aria-labelledby"],
)
async def test_fill_rejects_ambiguous_matching_fields(browser_instance, other_control):
    page = await browser_instance.new_page()
    await page.route("https://boards.greenhouse.io/**", lambda route: route.fulfill(
        body=f'<form><input name="email">{other_control}</form>',
        content_type="text/html",
    ))
    await page.goto("https://boards.greenhouse.io/jobs/ambiguous")
    with pytest.raises(AdapterDeferred, match="ambiguous"):
        await GreenhouseAdapter().fill(page, [FieldAnswer("email", "ada@example.test", [], "profile")])
    await page.close()


@pytest.mark.parametrize("adapter, host", [(GreenhouseAdapter(), "boards.greenhouse.io"), (LeverAdapter(), "jobs.lever.co")])
async def test_submit_is_explicit_and_unanswered_required_field_is_uncertain(adapter, host, browser_instance):
    page = await browser_instance.new_page()
    fixture = "lever.html" if host.endswith("lever.co") else "greenhouse.html"
    await page.route(f"https://{host}/**", lambda route: route.fulfill(path=FIXTURES / fixture))
    await page.goto(f"https://{host}/jobs/123")
    result = await adapter.submit(page)
    assert isinstance(result, SubmissionResult)
    assert result.status == "uncertain"
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


async def test_submit_does_not_click_when_required_control_is_unanswered(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><input name="email" type="email" required><button type="submit">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/incomplete")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert await page.locator("input").input_value() == ""
    assert await page.locator("form").is_visible()
    await page.close()


async def test_submit_does_not_click_when_aria_required_control_is_unanswered(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><input name="email" type="email" aria-required="true"><button type="submit">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/aria-incomplete")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert await page.locator("input").input_value() == ""
    await page.close()


async def test_submit_defers_for_custom_question_widget(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><div role="combobox" aria-required="true" aria-label="Country"></div><button type="submit">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/custom-required")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert "custom" in result.reason.lower()
    await page.close()


@pytest.mark.parametrize(
    "widget",
    [
        '<div role="switch" aria-required="true" aria-label="Consent"></div>',
        '<div role="listbox" aria-label="Country"></div>',
        '<div role="slider" aria-label="Experience"></div>',
        '<div role="spinbutton" aria-label="Years"></div>',
        '<div aria-required="true" aria-label="Custom response"></div>',
    ],
)
async def test_submit_defers_for_custom_aria_widgets_before_click(browser_instance, widget):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body=f'<form onsubmit="window.submitted = true; return false">{widget}<button type="submit">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/custom-aria")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert await page.evaluate("window.submitted === true") is False
    await page.close()


async def test_submit_defers_for_required_multi_select_before_click(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form onsubmit="window.submitted = true; return false"><select name="regions" multiple required><option selected value="west">West</option></select><button type="submit">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/multi-select")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert await page.evaluate("window.submitted === true") is False
    await page.close()


async def test_submit_does_not_click_with_unsupported_required_control(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><input name="date" type="date" required><button type="submit">Submit application</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/unsupported-required")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert await page.locator("input[type=date]").input_value() == ""
    await page.close()


async def test_submit_ignores_non_submit_button_with_expected_name(browser_instance):
    page = await browser_instance.new_page()
    await page.route(
        "https://boards.greenhouse.io/**",
        lambda route: route.fulfill(
            body='<form><button type="button">Apply</button></form>',
            content_type="text/html",
        ),
    )
    await page.goto("https://boards.greenhouse.io/jobs/not-submit")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert "submit control" in result.reason
    await page.close()


async def test_submit_blocks_off_allowlist_main_frame_redirect(browser_instance):
    page = await browser_instance.new_page()
    await page.route("https://boards.greenhouse.io/jobs/redirect", lambda route: route.fulfill(
        body='<form action="/leave"><button type="submit">Apply</button></form>',
        content_type="text/html",
    ))
    await page.route("https://boards.greenhouse.io/leave", lambda route: route.fulfill(
        status=302, headers={"location": "https://attacker.example/received"}, body=""
    ))
    await page.route("https://attacker.example/**", lambda route: route.fulfill(
        body="off-host", content_type="text/html"
    ))
    await page.goto("https://boards.greenhouse.io/jobs/redirect")
    result = await GreenhouseAdapter().submit(page)
    assert result.status == "uncertain"
    assert not page.url.startswith("https://attacker.example/")
    assert not await GreenhouseAdapter().confirm_submission(page)
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
