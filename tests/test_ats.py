"""Real headless-Chromium coverage of the ATS adapters.

NOTE on fixture provenance, since this matters for what a green run here
actually proves:

- tests/fixtures/ats/greenhouse.html and lever.html are still hand-written.
  Passing against them proves the adapter is internally consistent, not that
  it works against a real ATS page. Before trusting this against real
  applications, replace them with HTML saved from real postings and re-run
  `artemis apply` headed, without --submit, against a live posting (see the
  plan's verification section). Do not treat a green run here as that
  validation.
- tests/fixtures/ats/ashby.html is different: it is the real, unmodified,
  fully-rendered DOM saved from a live jobs.ashbyhq.com posting
  (Hebbia AI, backend-engineer-agent-collaboration-platform req), including
  its real (invisible) Google reCAPTCHA widget.
  tests/fixtures/ats/ashby_no_captcha.html is that same real fixture with
  only the reCAPTCHA widget's DOM/scripts stripped, so the fill/submit-gate
  behavior below can be exercised against real Ashby markup instead of a
  hand-written approximation of it.
  tests/fixtures/ats/ashby_visible_captcha.html is that same real fixture
  with only its reCAPTCHA iframe's `size=invisible` changed to `size=normal`
  (Google's own parameter for the checkbox-challenge variant), so the
  visible-CAPTCHA defer path is exercised against real markup too.
"""

import asyncio
from pathlib import Path

import pytest

from artemis.ats.ashby import AshbyAdapter
from artemis.ats.base import AdapterDeferred
from artemis.ats.greenhouse import GreenhouseAdapter
from artemis.ats.lever import LeverAdapter
from artemis.forms import FieldAnswer

FIXTURES = Path(__file__).parent / "fixtures" / "ats"


async def _load_fixture(context, host: str, filename: str, path: str = "/acme/jobs/1"):
    url = f"https://{host}{path}"
    html = (FIXTURES / filename).read_text(encoding="utf-8")

    async def handle(route):
        if route.request.url == url:
            await route.fulfill(status=200, content_type="text/html", body=html)
        else:
            await route.abort()

    await context.route("**/*", handle)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    return page


@pytest.fixture
async def browser():
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        pytest.skip("patchright is not installed")
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception:
            pytest.skip("chromium is not installed (run `patchright install chromium`)")
        try:
            yield browser
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_greenhouse_reads_native_and_combobox_questions(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    adapter = GreenhouseAdapter()

    questions = await adapter.read_questions(page)
    labels = {q.label: q for q in questions}

    assert "Email Address" in labels
    assert labels["Email Address"].kind == "email"
    assert "I agree to the terms of service" in labels
    assert labels["I agree to the terms of service"].kind == "checkbox"
    assert "Employment Type" in labels
    assert labels["Employment Type"].options == ["Full-time", "Part-time"]
    # The custom combobox must be read as a select, not skipped or fatal.
    assert "Are you authorized to work in the United States?" in labels
    combobox_question = labels["Are you authorized to work in the United States?"]
    assert combobox_question.kind == "select"
    assert combobox_question.options == ["Yes", "No"]

    await context.close()


@pytest.mark.asyncio
async def test_greenhouse_fills_native_fields_and_checkbox_without_submitting(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    adapter = GreenhouseAdapter()

    resume_path = Path(__file__)
    answers = [
        FieldAnswer("full_name", "Riley Example", "profile"),
        FieldAnswer("email", "riley@example.test", "profile"),
        FieldAnswer("agree_terms", "true", "profile"),
        FieldAnswer("employment_type", "Full-time", "profile"),
        FieldAnswer("resume", str(resume_path), "profile"),
    ]
    await adapter.fill(page, answers)

    assert await page.input_value("#email_field") == "riley@example.test"
    assert await page.is_checked("#agree_terms")
    assert await page.input_value("#employment_type") == "ft"
    submitted = await page.evaluate("document.body.getAttribute('data-submitted')")
    assert submitted is None

    await context.close()


@pytest.mark.asyncio
async def test_greenhouse_fills_custom_combobox(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    adapter = GreenhouseAdapter()

    await adapter.fill(page, [FieldAnswer("Are you authorized to work in the United States?", "Yes", "profile")])

    selected = await page.evaluate("document.getElementById('work_auth').getAttribute('data-selected')")
    assert selected == "Yes"

    await context.close()


@pytest.mark.asyncio
async def test_lever_reads_and_fills_without_submitting(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.lever.co", "lever.html")
    adapter = LeverAdapter()

    questions = await adapter.read_questions(page)
    labels = {q.label for q in questions}
    assert "Email" in labels

    resume_path = Path(__file__)
    await adapter.fill(
        page,
        [
            FieldAnswer("name", "Riley Example", "profile"),
            FieldAnswer("email", "riley@example.test", "profile"),
            FieldAnswer("resume", str(resume_path), "profile"),
        ],
    )
    assert await page.input_value("#email") == "riley@example.test"
    submitted = await page.evaluate("document.body.getAttribute('data-submitted')")
    assert submitted is None

    await context.close()


@pytest.mark.asyncio
async def test_ambiguous_answer_defers_rather_than_guessing(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.lever.co", "lever.html")
    adapter = LeverAdapter()

    with pytest.raises(AdapterDeferred):
        await adapter.fill(page, [FieldAnswer("no such field", "x", "profile")])

    await context.close()


_ASHBY_PATH = "/hebbia-ai/d4efb36c-c59b-4468-b572-88264d555167/application"


@pytest.mark.asyncio
async def test_ashby_reads_real_fields_despite_invisible_captcha(browser):
    """The unmodified real posting carries Google reCAPTCHA with
    size=invisible in its own iframe src (confirmed against the real
    markup): it never presents anything for a person to solve, so the
    agent must still read and fill the form rather than deferring on its
    mere presence. A *visible* challenge must still cause a defer -- see
    test_wait_until_ready_catches_delayed_captcha_injection for that case."""
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.ashbyhq.com", "ashby.html", _ASHBY_PATH)
    adapter = AshbyAdapter()

    questions = await adapter.read_questions(page)
    assert {q.label for q in questions} >= {"Name", "Email", "Resume", "Phone"}
    # The CAPTCHA's own hidden token-storage field is not an application
    # question and must never be offered to the user as one.
    assert not any(q.id == "g-recaptcha-response" for q in questions)

    await context.close()


@pytest.mark.asyncio
async def test_ashby_reads_real_fields_without_form_wrapper(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.ashbyhq.com", "ashby_no_captcha.html", _ASHBY_PATH)
    adapter = AshbyAdapter()

    questions = await adapter.read_questions(page)
    labels = {q.label: q for q in questions}

    assert labels.keys() >= {"Name", "Email", "Resume", "Phone", "Cover Letter", "LinkedIn Profile"}
    assert labels["Resume"].required is True
    assert labels["Resume"].kind == "file"
    assert labels["LinkedIn Profile"].required is False
    # The visually hidden, unaddressable "drop resume to autofill" convenience
    # input must not be surfaced as a question -- it has no name/id at all.
    assert "field-0" not in {q.id for q in questions}


@pytest.mark.asyncio
async def test_ashby_reads_yesno_widget_label_from_field_entry_wrapper(browser):
    """Regression guard for a real bug: Ashby's Yes/No question widget's
    underlying checkbox has a `name` but no `id`, so `label[for]` never
    matches it by `id` and the base adapter's native label detection falls
    back to the raw UUID `name`. This markup is a minimal hand-built snippet
    mirroring the real widget structure confirmed against a live posting
    (see AshbyAdapter._label_for_control's docstring), not the full real
    fixture -- the checked-in ashby fixtures don't include a Yes/No field."""
    context = await browser.new_context()
    html = """
    <div class="_fieldEntry_1e3gg_28 ashby-application-form-field-entry"
         data-field-path="ca6c86bc-7c4c-49cd-a40e-449655242c25">
      <label class="_heading_f7cvd_52 _required_f7cvd_91 _label_1e3gg_42 ashby-application-form-question-title"
             for="ca6c86bc-7c4c-49cd-a40e-449655242c25">
        Can you work from our office 4 days a week?
      </label>
      <div class="_container_1svni_28 _yesno_1e3gg_148 ashby-application-form-input-yesno">
        <button aria-pressed="false" data-option="yes">Yes</button>
        <button aria-pressed="false" data-option="no">No</button>
        <input type="checkbox" tabindex="-1" style="position:absolute;width:1px;height:1px;opacity:0;"
               name="ca6c86bc-7c4c-49cd-a40e-449655242c25">
      </div>
    </div>
    """
    url = "https://jobs.ashbyhq.com/acme/jobs/1"

    async def handle(route):
        if route.request.url == url:
            await route.fulfill(status=200, content_type="text/html", body=html)
        else:
            await route.abort()

    await context.route("**/*", handle)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    adapter = AshbyAdapter()

    questions = await adapter.read_questions(page)

    assert len(questions) == 1
    assert questions[0].label == "Can you work from our office 4 days a week?"

    # The checkbox is invisible (Ashby's real widget hides it this way); the
    # actual clickable UI is the sibling Yes/No button. Regression guard for a
    # real bug: filling this used to time out trying to click the invisible
    # checkbox directly. The listener is attached via page.evaluate rather
    # than an inline <script> tag -- fulfilled responses in this browser
    # don't execute inline scripts, so an inline handler would never fire
    # regardless of whether the click landed correctly.
    await page.evaluate(
        "document.querySelector('[data-option=yes]')"
        ".addEventListener('click', () => { window.__clicked = 'yes'; })"
    )
    await adapter.fill(
        page, [FieldAnswer("ca6c86bc-7c4c-49cd-a40e-449655242c25", "yes", "learned")]
    )
    assert await page.evaluate("window.__clicked") == "yes"

    await context.close()


@pytest.mark.asyncio
async def test_ashby_fills_real_fields_including_hidden_file_input_without_submitting(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.ashbyhq.com", "ashby_no_captcha.html", _ASHBY_PATH)
    adapter = AshbyAdapter()

    resume_path = Path(__file__)
    await adapter.fill(
        page,
        [
            FieldAnswer("_systemfield_name", "Riley Example", "profile"),
            FieldAnswer("_systemfield_email", "riley@example.test", "profile"),
            FieldAnswer("phone", "+1-555-010-2040", "profile"),
            FieldAnswer("_systemfield_resume", str(resume_path), "profile"),
        ],
    )

    assert await page.input_value("#_systemfield_name") == "Riley Example"
    assert await page.input_value("#_systemfield_email") == "riley@example.test"
    files = await page.locator("#_systemfield_resume").evaluate("el => el.files.length")
    assert files == 1
    # fill() itself never touches the submit control; this is a fill-only
    # adapter call, not `adapter.submit()`, which the pipeline gates separately.

    await context.close()


@pytest.mark.asyncio
async def test_ashby_submit_selector_matches_real_button(browser):
    """Regression guard: Ashby's submit control is a bare <button> with no
    <form> and no type='submit' -- it can only be found by its stable class."""
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.ashbyhq.com", "ashby_no_captcha.html", _ASHBY_PATH)
    adapter = AshbyAdapter()

    count = await page.locator(adapter.submit_selector).count()
    assert count == 1

    await context.close()


@pytest.mark.asyncio
async def test_ashby_defers_on_visible_captcha_challenge(browser):
    """A CAPTCHA a person must actually solve must still cause a defer.
    ashby_visible_captcha.html is the real fixture with only its reCAPTCHA
    iframe's `size=invisible` changed to `size=normal` (Google's own
    parameter for the checkbox-challenge variant) -- everything else is the
    real, unmodified posting."""
    context = await browser.new_context()
    page = await _load_fixture(context, "jobs.ashbyhq.com", "ashby_visible_captcha.html", _ASHBY_PATH)
    adapter = AshbyAdapter()

    with pytest.raises(AdapterDeferred, match="CAPTCHA"):
        await adapter.read_questions(page)

    await context.close()


@pytest.mark.asyncio
async def test_wait_until_ready_catches_delayed_captcha_injection(browser):
    """Regression test for a real bug: navigating with wait_until='domcontentloaded'
    against a client-rendered page can complete before that page's JS has injected
    its form OR its CAPTCHA widget. Checking the access wall at that instant does
    not mean the page is clear -- it means the check ran too early to see it. This
    serves a visible-challenge fixture after an artificial delay, so `read_questions`
    (which must call `wait_until_ready` first) still sees the real CAPTCHA."""
    context = await browser.new_context()
    url = f"https://jobs.ashbyhq.com{_ASHBY_PATH}"
    html = (FIXTURES / "ashby_visible_captcha.html").read_text(encoding="utf-8")

    async def handle_delayed(route):
        if route.request.url == url:
            await route.fulfill(status=200, content_type="text/html", body="<html><body></body></html>")
        else:
            await route.abort()

    await context.route("**/*", handle_delayed)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")

    async def inject_after_delay():
        await asyncio.sleep(0.3)
        await page.evaluate("html => { document.open(); document.write(html); document.close(); }", html)

    adapter = AshbyAdapter()
    inject_task = asyncio.create_task(inject_after_delay())
    try:
        with pytest.raises(AdapterDeferred, match="CAPTCHA"):
            await adapter.read_questions(page)
    finally:
        await inject_task

    await context.close()


@pytest.mark.asyncio
async def test_waits_for_recaptcha_iframe_before_judging_invisible_vs_visible(browser):
    """Regression test for a real bug found live: the reCAPTCHA loader
    script can exist in the DOM before the widget iframe it injects does
    (~1s gap, confirmed against the real posting). Judging invisible-vs-
    visible the instant the loader is seen but the iframe is not yet
    misclassifies a real invisible widget as an unrecognized, and therefore
    "visible", one -- causing a false defer on a form that is actually
    fillable. This serves the real fixture with its reCAPTCHA iframe removed
    but its loader script kept, then injects a `size=invisible` iframe after
    a delay, and asserts `read_questions` waits for it rather than
    defer-on-loader-alone."""
    context = await browser.new_context()
    url = f"https://jobs.ashbyhq.com{_ASHBY_PATH}"
    html = (FIXTURES / "ashby_no_captcha.html").read_text(encoding="utf-8")
    # ashby_no_captcha.html strips the loader script too; add one back so
    # this exercises the "loader present, iframe not yet" state specifically.
    html = html.replace(
        "</body>", '<script id="recaptchaScript" src="https://www.recaptcha.net/recaptcha/api.js"></script></body>'
    )

    async def handle(route):
        if route.request.url == url:
            await route.fulfill(status=200, content_type="text/html", body=html)
        else:
            await route.abort()

    await context.route("**/*", handle)
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")

    async def inject_iframe_after_delay():
        await asyncio.sleep(0.3)
        await page.evaluate(
            """() => {
              const iframe = document.createElement('iframe');
              iframe.src = 'https://www.recaptcha.net/recaptcha/api2/anchor?size=invisible&k=test';
              document.body.appendChild(iframe);
            }"""
        )

    adapter = AshbyAdapter()
    inject_task = asyncio.create_task(inject_iframe_after_delay())
    try:
        questions = await adapter.read_questions(page)
        assert {q.label for q in questions} >= {"Name", "Email", "Resume", "Phone"}
    finally:
        await inject_task

    await context.close()


@pytest.mark.asyncio
async def test_paced_fill_produces_the_same_values_as_instant_fill(browser):
    from artemis.pacing import HumanPacer

    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")

    async def _instant_sleep(_seconds: float) -> None:
        return None

    pacer = HumanPacer(seed=7, sleep=_instant_sleep)
    adapter = GreenhouseAdapter(pacer=pacer)

    resume_path = Path(__file__)
    answers = [
        FieldAnswer("full_name", "Riley Example", "profile"),
        FieldAnswer("email", "riley@example.test", "profile"),
        FieldAnswer("agree_terms", "true", "profile"),
        FieldAnswer("employment_type", "Full-time", "profile"),
        FieldAnswer("resume", str(resume_path), "profile"),
    ]
    await adapter.fill(page, answers)

    assert await page.input_value("#email_field") == "riley@example.test"
    assert await page.is_checked("#agree_terms")
    assert await page.input_value("#employment_type") == "ft"
    submitted = await page.evaluate("document.body.getAttribute('data-submitted')")
    assert submitted is None

    await context.close()


@pytest.mark.asyncio
async def test_paced_submit_still_defers_on_ambiguous_submit_control(browser):
    from artemis.pacing import HumanPacer

    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    await page.evaluate(
        """() => {
          const extra = document.createElement('button');
          extra.type = 'submit';
          document.querySelector('form').appendChild(extra);
        }"""
    )

    async def _instant_sleep(_seconds: float) -> None:
        return None

    adapter = GreenhouseAdapter(pacer=HumanPacer(seed=1, sleep=_instant_sleep))
    result = await adapter.submit(page)

    assert result.status == "uncertain"

    await context.close()


async def _fill_required_greenhouse_fields(page) -> None:
    # Required fields block native form submission until filled.
    adapter = GreenhouseAdapter()
    answers = [
        FieldAnswer("full_name", "Riley Example", "profile"),
        FieldAnswer("email", "riley@example.test", "profile"),
        FieldAnswer("agree_terms", "true", "profile"),
        FieldAnswer("resume", str(Path(__file__)), "profile"),
    ]
    await adapter.fill(page, answers)


@pytest.mark.asyncio
async def test_submit_without_a_confirmation_signal_is_uncertain_not_confirmed(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    await _fill_required_greenhouse_fields(page)
    adapter = GreenhouseAdapter()

    result = await adapter.submit(page)

    assert result.status == "uncertain"
    submitted = await page.evaluate("document.body.getAttribute('data-submitted')")
    assert submitted == "true"  # the click itself did happen

    await context.close()


@pytest.mark.asyncio
async def test_submit_confirmed_when_a_confirmation_element_appears(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    await _fill_required_greenhouse_fields(page)
    await page.evaluate(
        """() => {
          document.getElementById('application-form').addEventListener('submit', () => {
            const confirmation = document.createElement('div');
            confirmation.id = 'confirmation';
            confirmation.textContent = 'Thanks for applying!';
            document.body.appendChild(confirmation);
          });
        }"""
    )
    adapter = GreenhouseAdapter()

    result = await adapter.submit(page)

    assert result.status == "confirmed"

    await context.close()


@pytest.mark.asyncio
async def test_submit_confirmed_when_the_page_navigates_away(browser):
    context = await browser.new_context()
    page = await _load_fixture(context, "boards.greenhouse.io", "greenhouse.html")
    await _fill_required_greenhouse_fields(page)

    async def handle_thanks(route):
        await route.fulfill(status=200, content_type="text/html", body="<html>Thanks!</html>")

    await page.route("**/thanks", handle_thanks)
    await page.evaluate(
        """() => {
          document.getElementById('application-form').addEventListener('submit', () => {
            window.location.href = '/thanks';
          });
        }"""
    )
    adapter = GreenhouseAdapter()

    result = await adapter.submit(page)

    assert result.status == "confirmed"

    await context.close()
