"""Shared Playwright behavior for supported ATS platforms.

`fill()` never clicks anything that submits the form -- submission is a separate,
explicit call the pipeline only makes when the caller opted in. Every method
raises `AdapterDeferred` for anything it cannot safely handle (a CAPTCHA, a login
wall, an ambiguous field match, more than one form on the page): silence here would
mean guessing, and guessing on someone's real application is the one thing this
project must not do.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from urllib.parse import urlsplit

from artemis.forms import FieldAnswer, FormQuestion
from artemis.pacing import NullPacer, Pacer

SubmissionStatus = Literal["confirmed", "rejected", "uncertain"]


@dataclass(frozen=True)
class SubmissionResult:
    """Outcome from an explicitly requested submit attempt."""

    status: SubmissionStatus
    reason: str


class AdapterDeferred(RuntimeError):
    """Raised when a page contains something the adapter cannot safely handle."""


class ATSAdapter(Protocol):
    """Browser operations shared by the supported ATS platforms."""

    def supports_url(self, url: str) -> bool: ...

    def matches(self, page) -> bool: ...

    async def wait_until_ready(self, page) -> None: ...

    async def read_questions(self, page) -> list[FormQuestion]: ...

    async def fill(self, page, answers: Sequence[FieldAnswer]) -> None: ...

    async def submit(self, page) -> SubmissionResult: ...

    async def confirm_submission(self, page, pre_submit_url: str) -> bool: ...


# A control's accessible role, when it's not a native <select>. Real Greenhouse
# and Lever forms render single-select dropdowns as one of these; treating them
# as unsupported (as opposed to reading their expanded option list) means the
# adapter would defer on most real postings.
_COMBOBOX_ROLE_SELECTOR = "[role='combobox'], [role='listbox']"


class BaseATSAdapter:
    """Label-first form reading and filling, with no implicit submission.

    `form_selector` scopes every read/fill to a specific container. Set it to
    `None` for a platform that renders its application fields without a
    `<form>` wrapper at all (Ashby does this) -- controls are then located
    across the whole page instead of requiring exactly one `<form>` element.
    """

    allowed_hosts: tuple[str, ...] = ()
    form_selector: str | None = "form"
    navigation_timeout_ms = 15_000
    action_timeout_ms = 5_000
    submit_selector = "button[type='submit'], input[type='submit']"
    # Cross-vendor confirmation markers, checked only after a submit click;
    # their absence means "unconfirmed", not "failed" (see confirm_submission).
    confirmation_selectors: tuple[str, ...] = (
        "[data-qa='application-confirmation']",
        "#confirmation",
        ".application-confirmation",
        "[role='status']",
    )
    _CONFIRMATION_APPEAR_TIMEOUT_MS = 5_000

    def __init__(self, *, pacer: Pacer | None = None) -> None:
        self.pacer: Pacer = pacer or NullPacer()

    def matches(self, page) -> bool:
        try:
            return self.supports_url(page.url)
        except (AttributeError, ValueError):
            return False

    def supports_url(self, url: str) -> bool:
        """Return whether a URL is on this adapter's exact HTTPS board host."""
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            return parsed.scheme == "https" and hostname in self.allowed_hosts
        except ValueError:
            return False

    def _require_supported_page(self, page) -> None:
        if not self.matches(page):
            raise AdapterDeferred("page is outside the HTTPS host allowlist")
        page.set_default_timeout(self.action_timeout_ms)
        page.set_default_navigation_timeout(self.navigation_timeout_ms)

    async def wait_until_ready(self, page) -> None:
        """Wait for the page's real content to exist before anything reads it.

        Many ATS pages (Ashby among them) are client-rendered: right after
        navigation completes, the DOM has no form controls and, critically,
        no CAPTCHA/login-wall markup either -- both are injected by JS a
        moment later. Checking `_check_access_wall` before that JS has run
        does not mean the page is clear; it means the check hasn't looked at
        the real page yet. This must be called (by the caller, right after
        navigation) before any read, fill, or access-wall check.

        A page that never produces a real control (unsupported flow, or
        genuinely blank) times out into a defer rather than hanging forever.
        """
        self._require_supported_page(page)
        try:
            await page.wait_for_selector(
                "input:not([type=hidden]), textarea, select, " + _COMBOBOX_ROLE_SELECTOR,
                timeout=self.navigation_timeout_ms,
                state="attached",
            )
        except Exception as error:
            raise AdapterDeferred("no application form appeared after navigation") from error
        await self.pacer.after_load()

    # Google's reCAPTCHA loader script is present on any page that will ever
    # render the widget, well before the widget iframe itself exists (~1s
    # after the loader runs, confirmed against a real posting). Its mere
    # presence is evidence a widget may appear, never evidence of a
    # challenge -- so it must never itself match the generic captcha-shaped
    # catch-all below.
    _RECAPTCHA_LOADER_SELECTOR = "script[src*='recaptcha'], script#recaptchaScript"
    _RECAPTCHA_IFRAME_SELECTOR = "iframe[src*='recaptcha']"
    _RECAPTCHA_APPEAR_TIMEOUT_MS = 5_000
    # Google's own hidden textarea that receives the solved widget's token
    # (`name="g-recaptcha-response"`, standard across any site using the
    # widget). It is never an application question, so it must be excluded
    # from read_questions -- not just left unresolved, since it should never
    # be offered to the user as something to answer at all.
    _RECAPTCHA_RESPONSE_SELECTOR = "[name='g-recaptcha-response']"

    async def _has_visible_captcha_challenge(self, page) -> bool:
        """Whether a CAPTCHA is present that a person must actively solve.

        A background-scoring widget (Google reCAPTCHA v2 "invisible", or v3)
        never presents anything for a person to click or type -- it silently
        scores request/behavior signals and only escalates to a visible
        challenge if that score is poor. Deferring on its mere presence would
        mean deferring on most modern job-application forms, since sites
        commonly enable it without ever surfacing a challenge to a normal
        visitor. What must still cause a defer is a widget that *does* ask a
        person to act: a checkbox challenge, an image challenge, or any
        CAPTCHA-like element this adapter cannot positively identify as
        invisible -- unknown means treat it as visible, not the reverse.

        Google's own API marks this in the widget iframe's own `src`:
        `size=invisible` for the non-interactive variant; `size=normal` (or
        the parameter's absence, which defaults to `normal`) for the
        checkbox challenge a person must click. This is Google's documented
        reCAPTCHA parameter, not a site-specific convention, so it holds
        across any site using the standard widget -- confirmed here against
        a real posting's own invisible reCAPTCHA markup.

        The reCAPTCHA loader script can be in the DOM before the widget
        iframe it will inject exists yet (both are added by the site's own
        JS, on their own separate schedules, and the loader typically runs
        first). Deciding the instant the loader is seen but the iframe is not
        would misjudge a real invisible widget as visible purely because it
        hasn't rendered yet -- so this waits briefly for the iframe once the
        loader indicates one is coming, rather than treating "not yet
        rendered" the same as "no widget".
        """
        if await self._wait_for_recaptcha_iframe(page):
            recaptcha_iframe = page.locator(self._RECAPTCHA_IFRAME_SELECTOR)
            for index in range(await recaptcha_iframe.count()):
                src = await recaptcha_iframe.nth(index).get_attribute("src") or ""
                if "size=invisible" not in src:
                    return True
            return False

        # No recognized reCAPTCHA iframe appeared. Any other CAPTCHA-shaped
        # marker (hCaptcha, an unlabeled data-sitekey widget, a generic
        # captcha iframe) is treated as visible: this adapter has no
        # grounded basis for calling it invisible. The loader script tag
        # itself is excluded -- it precedes any widget and is not one.
        other_captcha = page.locator(
            "iframe[src*='captcha'], iframe[title*='captcha' i], "
            ".g-recaptcha, [data-sitekey], [id*='captcha' i]:not(script)"
        )
        return bool(await other_captcha.count())

    async def _wait_for_recaptcha_iframe(self, page) -> bool:
        """Return whether a reCAPTCHA iframe exists, waiting briefly if a
        loader script says one is on its way but hasn't rendered yet."""
        if await page.locator(self._RECAPTCHA_IFRAME_SELECTOR).count():
            return True
        if not await page.locator(self._RECAPTCHA_LOADER_SELECTOR).count():
            return False
        try:
            await page.wait_for_selector(
                self._RECAPTCHA_IFRAME_SELECTOR, timeout=self._RECAPTCHA_APPEAR_TIMEOUT_MS, state="attached"
            )
            return True
        except Exception:
            # The loader never produced a widget within the wait -- proceed
            # as if there is none, rather than blocking indefinitely.
            return False

    async def _check_access_wall(self, page) -> None:
        if await self._has_visible_captcha_challenge(page):
            raise AdapterDeferred("CAPTCHA detected")

        password = page.locator("input[type='password']")
        body = (await page.locator("body").inner_text()).lower()
        if await password.count() or any(
            phrase in body for phrase in ("sign in to continue", "log in to continue", "login required")
        ):
            raise AdapterDeferred("login wall detected")

    async def _require_form_scope(self, page):
        """Return the container all reads/fills are scoped to, or defer.

        Called by every one of read_questions/fill/submit, so the ready-wait
        and access-wall check happen no matter which entry point a caller
        uses -- neither is a convention callers must remember to invoke first.

        When `form_selector` is None the whole page is the scope: a platform
        with no `<form>` wrapper has nothing to count or disambiguate here,
        so this only enforces the ready-wait and access-wall check in that case.
        """
        await self.wait_until_ready(page)
        await self._check_access_wall(page)
        if self.form_selector is None:
            return page.locator(":root")
        form = page.locator(self.form_selector)
        if await form.count() != 1:
            raise AdapterDeferred("application form is missing or ambiguous")
        return form

    async def read_questions(self, page) -> list[FormQuestion]:
        self._require_supported_page(page)
        scope = await self._require_form_scope(page)

        questions: list[FormQuestion] = []
        native = scope.locator(
            f"input:not([type=hidden]):not({self._RECAPTCHA_RESPONSE_SELECTOR}), "
            f"textarea:not({self._RECAPTCHA_RESPONSE_SELECTOR}), select"
        )
        for index in range(await native.count()):
            control = native.nth(index)
            if await self._is_decorative(control):
                # Not a real question: e.g. Ashby's visually hidden, unfocusable
                # "drop your resume here to autofill" convenience input layered
                # on top of the actual form fields. Filling it would be a no-op
                # at best; surfacing it as an unresolved field would only confuse.
                continue
            questions.append(await self._read_native_control(control))

        comboboxes = scope.locator(_COMBOBOX_ROLE_SELECTOR)
        for index in range(await comboboxes.count()):
            question = await self._read_combobox(comboboxes.nth(index))
            if question is not None:
                questions.append(question)

        return questions

    @staticmethod
    async def _is_decorative(control) -> bool:
        """Whether a control is a UI affordance rather than a real question.

        Some platforms visually hide a functional file input behind a styled
        drop zone (clip/1px sizing, tabindex=-1) -- that pattern alone does
        not mean "not a real field," since the visible resume-upload control
        is often built exactly this way. What does distinguish a decorative
        control is having no `name` or `id` at all: nothing else in this
        codebase can address it by question_id, so it could never be filled
        or reported on meaningfully even if surfaced. A real question, however
        it is styled, is always given a stable identity by the platform.
        """
        return await control.evaluate(
            "element => !element.name && !element.id"
        )

    async def _label_for_control(self, control) -> str | None:
        """Hook for a platform-specific label strategy.

        Returns a label string to use in place of `_read_native_control`'s
        own label detection, or None to fall back to that default (native
        `<label>`/ARIA/legend association). Ashby overrides this because some
        of its controls (e.g. a Yes/No widget's checkbox) have a `name` but no
        `id`, so `label[for]` never matches them by `id`.
        """
        return None

    async def _fill_hidden_checkbox(self, control, checked: bool) -> bool:
        """Hook for a platform whose checkbox is a hidden proxy for other UI.

        Returns True if it handled the fill itself, False to fall back to the
        default `set_checked` on the control. Ashby overrides this because its
        Yes/No widget's checkbox is not the clickable element -- a sibling
        `<button data-option="yes|no">` is -- so `set_checked` times out
        waiting for a checkbox that is never meant to be visible.
        """
        return False

    async def _read_native_control(self, control) -> FormQuestion:
        details = await control.evaluate(
            """element => {
              const labelled = element.labels ? Array.from(element.labels) : [];
              const fieldset = element.closest('fieldset');
              const legend = fieldset && fieldset.querySelector('legend');
              let label = labelled.map(item => item.innerText).join(' ').trim();
              if (element.type === 'checkbox' && legend) label = legend.innerText.trim();
              if (!label) label = element.getAttribute('aria-label') || '';
              if (!label && element.hasAttribute('aria-labelledby')) {
                label = element.getAttribute('aria-labelledby').split(/\\s+/)
                  .map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
              }
              const options = element.tagName === 'SELECT'
                ? Array.from(element.options).filter(option => option.value !== '')
                    .map(option => option.label.trim())
                : [];
              const siblingIndex = element.form
                ? Array.from(element.form.elements).indexOf(element)
                : Array.from(document.querySelectorAll('input, textarea, select')).indexOf(element);
              return {
                id: element.name || element.id || ('field-' + siblingIndex),
                label,
                required: element.required || element.getAttribute('aria-required') === 'true',
                tag: element.tagName.toLowerCase(),
                type: element.type || '',
                multiple: element.multiple === true,
                options,
                maxLength: element.maxLength > 0 ? element.maxLength : null
              };
            }"""
        )
        kind = "unknown" if details["multiple"] else self._question_kind(details["tag"], details["type"])
        label = await self._label_for_control(control) or details["label"] or details["id"]
        return FormQuestion(
            id=details["id"],
            label=label,
            required=details["required"],
            kind=kind,
            options=details["options"],
            max_length=details["maxLength"],
        )

    async def _read_combobox(self, control) -> FormQuestion | None:
        """Read a custom single-select rendered as role=combobox/listbox.

        Real ATS boards (Greenhouse in particular) render dropdown questions
        this way rather than as a native <select>. Its option list only exists
        in the DOM once expanded, so this reads the option text already present
        (aria-owns / associated listbox), and never opens the control itself --
        opening it is a UI side effect `read_questions` must not cause.
        """
        details = await control.evaluate(
            """element => {
              const label = element.getAttribute('aria-label')
                || (element.getAttribute('aria-labelledby') || '').split(/\\s+/)
                    .map(id => document.getElementById(id)?.innerText || '').join(' ').trim()
                || element.closest('label')?.innerText?.trim()
                || '';
              const listId = element.getAttribute('aria-owns') || element.getAttribute('aria-controls');
              const list = listId ? document.getElementById(listId) : null;
              const options = list
                ? Array.from(list.querySelectorAll('[role=\"option\"]')).map(o => o.innerText.trim())
                : [];
              return {
                id: element.id || element.getAttribute('name') || '',
                label,
                required: element.getAttribute('aria-required') === 'true',
                options,
              };
            }"""
        )
        if not details["id"] or not details["label"]:
            return None
        return FormQuestion(
            id=details["id"],
            label=details["label"],
            required=details["required"],
            kind="select",
            options=details["options"],
            max_length=None,
        )

    @staticmethod
    def _question_kind(tag: str, input_type: str) -> str:
        if tag == "select":
            return "select"
        if tag == "textarea":
            return "text"
        if input_type == "email":
            return "email"
        if input_type == "tel":
            return "phone"
        if input_type == "checkbox":
            return "checkbox"
        if input_type == "file":
            return "file"
        if input_type in {"text", "search", "url"}:
            return "text"
        return "unknown"

    async def _answer_locator(self, page, answer: FieldAnswer):
        """Find the one control matching an answer's question_id, or defer.

        More than one match is treated the same as zero: writing a value into
        the wrong one of two ambiguous fields is worse than not filling either.
        """
        scope = page.locator(self.form_selector) if self.form_selector is not None else page
        controls = scope.locator(
            "input:not([type=hidden]), textarea, select, " + _COMBOBOX_ROLE_SELECTOR
        )
        matches = []
        question_id = " ".join(answer.question_id.split()).casefold()
        for index in range(await controls.count()):
            control = controls.nth(index)
            identity = await control.evaluate(
                "element => ({name: element.name || '', id: element.id || '', labels: ["
                "...Array.from(element.labels || []).map(label => label.textContent || ''), "
                "element.getAttribute('aria-label') || '', "
                "...(element.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)"
                ".map(id => document.getElementById(id)?.textContent || '')]})"
            )
            identifiers = [identity["name"], identity["id"], *identity["labels"]]
            if question_id in {
                " ".join(identifier.split()).casefold() for identifier in identifiers if identifier
            }:
                matches.append(control)
        if len(matches) > 1:
            raise AdapterDeferred(f"answer {answer.question_id!r} matches ambiguous fields")
        if matches:
            return matches[0]
        raise AdapterDeferred(f"no supported field matches answer {answer.question_id!r}")

    async def fill(self, page, answers: Sequence[FieldAnswer]) -> None:
        """Fill supported controls only. This method never clicks a submit control."""
        self._require_supported_page(page)
        await self._require_form_scope(page)

        seen: set[str] = set()
        for answer in answers:
            if answer.question_id in seen:
                raise AdapterDeferred(f"duplicate answer for {answer.question_id!r}")
            seen.add(answer.question_id)
            control = await self._answer_locator(page, answer)
            await self._fill_one(control, answer)
            await self.pacer.think()

    async def _fill_one(self, control, answer: FieldAnswer) -> None:
        details = await control.evaluate(
            "element => ({tag: element.tagName.toLowerCase(), type: element.type || '', "
            "multiple: element.multiple === true, role: element.getAttribute('role') || ''})"
        )
        tag, input_type, role = details["tag"], details["type"], details["role"]
        if details["multiple"]:
            raise AdapterDeferred(f"unsupported multi-value control for {answer.question_id!r}")

        if input_type == "file":
            from pathlib import Path

            resume_path = Path(answer.value)
            if not resume_path.is_file():
                raise AdapterDeferred("resume file is missing or invalid")
            try:
                await control.set_input_files(str(resume_path))
            except Exception as error:
                raise AdapterDeferred("resume file could not be attached") from error
            attached = await control.evaluate("element => element.files.length > 0")
            if not attached:
                raise AdapterDeferred("resume file did not attach to the upload control")
        elif tag == "select":
            try:
                await control.select_option(label=answer.value)
            except Exception as error:
                raise AdapterDeferred(f"unsupported select option for {answer.question_id!r}") from error
        elif role in {"combobox", "listbox"}:
            await self._fill_combobox(control, answer)
        elif input_type == "checkbox":
            checked = answer.value.strip().casefold() in {"1", "true", "yes", "on", "checked"}
            if not await self._fill_hidden_checkbox(control, checked):
                await self.pacer.before_click(control)
                await control.set_checked(checked)
        elif tag == "textarea" or (tag == "input" and input_type in {"text", "email", "tel", "url", "search"}):
            await self.pacer.before_click(control)
            await self.pacer.type_text(control, answer.value)
        else:
            raise AdapterDeferred(f"unsupported control for {answer.question_id!r}")

    async def _fill_combobox(self, control, answer: FieldAnswer) -> None:
        """Open a custom dropdown, type or click to the matching option, then close it."""
        from artemis.mapping import normalize_label

        await self.pacer.before_click(control)
        await control.click()
        option = control.page.get_by_role("option", name=answer.value, exact=False)
        try:
            count = await option.count()
        except Exception as error:
            raise AdapterDeferred(f"unsupported combobox option for {answer.question_id!r}") from error
        matching = [
            option.nth(i)
            for i in range(count)
            if normalize_label(await option.nth(i).inner_text()) == normalize_label(answer.value)
        ]
        if len(matching) != 1:
            raise AdapterDeferred(f"unsupported combobox option for {answer.question_id!r}")
        await self.pacer.before_click(matching[0])
        await matching[0].click()

    async def submit(self, page) -> SubmissionResult:
        """Click the form's submit control. "confirmed" requires positive
        evidence the submission was received, not just that the click
        happened -- see `confirm_submission`."""
        self._require_supported_page(page)
        await self._require_form_scope(page)
        button = page.locator(self.submit_selector)
        if await button.count() != 1:
            return SubmissionResult("uncertain", "submit control is missing or ambiguous")
        pre_submit_url = page.url
        await self.pacer.before_submit()
        await self.pacer.before_click(button.first)
        await button.first.click()
        if await self.confirm_submission(page, pre_submit_url):
            return SubmissionResult("confirmed", "submission confirmed after click")
        return SubmissionResult(
            "uncertain", "submit control was clicked but no confirmation was observed"
        )

    async def confirm_submission(self, page, pre_submit_url: str) -> bool:
        """A confirmation element appeared, or the page navigated away
        (most ATS vendors redirect on success)."""
        try:
            await page.wait_for_selector(
                ", ".join(self.confirmation_selectors),
                timeout=self._CONFIRMATION_APPEAR_TIMEOUT_MS,
                state="visible",
            )
            return True
        except Exception:
            pass
        return page.url != pre_submit_url
