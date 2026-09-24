"""Shared safety and Playwright behavior for supported ATS adapters."""

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence
from urllib.parse import urljoin, urlsplit

from jobapply.forms import FieldAnswer, FormQuestion


SubmissionStatus = Literal["confirmed", "rejected", "uncertain"]


@dataclass(frozen=True)
class SubmissionResult:
    """Outcome from an explicitly requested submit attempt."""

    status: SubmissionStatus
    reason: str


class AdapterDeferred(RuntimeError):
    """Raised when an application page contains a flow the adapter cannot safely handle."""


class ATSAdapter(Protocol):
    """Browser operations shared by the supported ATS platforms."""

    def supports_url(self, url: str) -> bool: ...

    def matches(self, page) -> bool: ...

    async def read_questions(self, page) -> list[FormQuestion]: ...

    async def read_job_context(self, page) -> str | None: ...

    async def fill(self, page, answers: Sequence[FieldAnswer]) -> None: ...

    async def verify_resume_upload(self, page, question_id: str) -> bool: ...

    async def submit(self, page) -> SubmissionResult: ...

    async def confirm_submission(self, page) -> bool: ...


class BaseATSAdapter:
    """Strict, label-first form operations with no implicit submission."""

    allowed_hosts: tuple[str, ...] = ()
    form_selector = "form"
    context_selectors = ("#job-description", ".posting-description", "[data-qa='job-description']")
    confirmation_selectors = (
        "[data-qa='application-confirmation']",
        "#confirmation",
        ".application-confirmation",
        "[role='status']",
    )
    navigation_timeout_ms = 15_000
    action_timeout_ms = 5_000
    native_submit_selector = (
        "button[type='submit'], button:not([type]), input[type='submit'], input[type='image']"
    )

    def matches(self, page) -> bool:
        """Accept only HTTPS pages on an explicitly supported board host."""
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

    def _is_allowed_url(self, url: str) -> bool:
        return self.supports_url(url)

    def _require_supported_page(self, page) -> None:
        if not self.matches(page):
            raise AdapterDeferred("page is outside the HTTPS host allowlist")
        page.set_default_timeout(self.action_timeout_ms)
        page.set_default_navigation_timeout(self.navigation_timeout_ms)

    async def _check_access_wall(self, page) -> None:
        captcha = page.locator(
            "iframe[src*='captcha'], iframe[title*='captcha' i], "
            ".g-recaptcha, [data-sitekey], [id*='captcha' i]"
        )
        if await captcha.count():
            raise AdapterDeferred("CAPTCHA detected")

        password = page.locator("input[type='password']")
        body = (await page.locator("body").inner_text()).lower()
        if await password.count() or any(
            phrase in body for phrase in ("sign in to continue", "log in to continue", "login required")
        ):
            raise AdapterDeferred("login wall detected")

    async def read_questions(self, page) -> list[FormQuestion]:
        self._require_supported_page(page)
        await self._check_access_wall(page)
        forms = page.locator(self.form_selector)
        if await forms.count() != 1:
            raise AdapterDeferred("application form is missing or ambiguous")

        custom_controls = forms.locator(
            "[role='combobox'], [role='radio'], [role='checkbox'], [role='switch'], "
            "[role='listbox'], [role='slider'], [role='spinbutton'], [contenteditable='true'], "
            "[aria-required='true']:not(input):not(select):not(textarea)"
        )
        if await custom_controls.count():
            raise AdapterDeferred("unsupported custom question control detected")

        controls = forms.locator("input:not([type=hidden]), textarea, select")
        questions: list[FormQuestion] = []
        for index in range(await controls.count()):
            control = controls.nth(index)
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
                  return {
                    id: element.name || element.id || ('field-' + Array.from(element.form.elements).indexOf(element)),
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
            kind = (
                "unknown"
                if details["multiple"]
                else self._question_kind(details["tag"], details["type"])
            )
            questions.append(
                FormQuestion(
                    id=details["id"],
                    label=details["label"] or details["id"],
                    required=details["required"],
                    kind=kind,
                    options=details["options"],
                    max_length=details["maxLength"],
                )
            )
        return questions

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
        if input_type in {"text", "search", "url"}:
            return "text"
        return "unknown"

    async def read_job_context(self, page) -> str | None:
        self._require_supported_page(page)
        for selector in self.context_selectors:
            candidate = page.locator(selector)
            if await candidate.count() == 1:
                text = (await candidate.inner_text()).strip()
                if text:
                    return text[:20_000]
        return None

    async def _answer_locator(self, page, answer: FieldAnswer):
        controls = page.locator(self.form_selector).locator(
            "input:not([type=hidden]), textarea, select"
        )
        matches = []
        question_id = " ".join(answer.question_id.split()).casefold()
        for index in range(await controls.count()):
            control = controls.nth(index)
            identity = await control.evaluate(
                "element => ({name: element.name, id: element.id, labels: ["
                "...Array.from(element.labels || []).map(label => label.textContent || ''), "
                "element.getAttribute('aria-label') || '', "
                "...(element.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)"
                ".map(id => document.getElementById(id)?.textContent || '')]})"
            )
            identifiers = [identity["name"], identity["id"], *identity["labels"]]
            if question_id in {
                " ".join(identifier.split()).casefold()
                for identifier in identifiers
                if identifier
            }:
                matches.append(control)
        if len(matches) > 1:
            raise AdapterDeferred(f"answer {answer.question_id!r} matches ambiguous fields")
        if matches:
            return matches[0]

        labelled = page.get_by_label(answer.question_id, exact=True)
        count = await labelled.count()
        if count > 1:
            raise AdapterDeferred(f"answer {answer.question_id!r} matches ambiguous fields")
        if count == 1:
            return labelled
        raise AdapterDeferred(f"no supported field matches answer {answer.question_id!r}")

    async def fill(self, page, answers: Sequence[FieldAnswer]) -> None:
        """Fill supported controls only. This method never clicks a submit control."""
        self._require_supported_page(page)
        await self._check_access_wall(page)
        form = page.locator(self.form_selector)
        if await form.count() != 1:
            raise AdapterDeferred("application form is missing or ambiguous")

        seen: set[str] = set()
        for answer in answers:
            if answer.question_id in seen:
                raise AdapterDeferred(f"duplicate answer for {answer.question_id!r}")
            seen.add(answer.question_id)
            control = await self._answer_locator(page, answer)
            details = await control.evaluate(
                "element => ({tag: element.tagName.toLowerCase(), type: element.type || '', "
                "multiple: element.multiple === true})"
            )
            tag, input_type = details["tag"], details["type"]
            if details["multiple"]:
                raise AdapterDeferred(f"unsupported multi-value control for {answer.question_id!r}")
            if input_type == "file":
                try:
                    from pathlib import Path

                    resume_path = Path(answer.value)
                    if not resume_path.is_file():
                        raise ValueError("not a regular file")
                    await control.set_input_files(str(resume_path))
                except (OSError, ValueError) as error:
                    raise AdapterDeferred("resume file is missing or invalid") from error
            elif tag == "select":
                try:
                    await control.select_option(label=answer.value)
                except Exception as error:
                    raise AdapterDeferred(f"unsupported select option for {answer.question_id!r}") from error
            elif input_type == "checkbox":
                checked = answer.value.strip().casefold() in {"1", "true", "yes", "on", "checked"}
                await control.set_checked(checked)
            elif tag == "textarea" or (
                tag == "input" and input_type in {"text", "email", "tel", "url", "search"}
            ):
                await control.fill(answer.value)
            else:
                raise AdapterDeferred(f"unsupported control for {answer.question_id!r}")

    async def verify_resume_upload(self, page, question_id: str) -> bool:
        """Confirm that the identified file input has a selected local file."""
        self._require_supported_page(page)
        try:
            control = await self._answer_locator(
                page, FieldAnswer(question_id, "", [], "profile")
            )
            return await control.evaluate(
                "element => element.tagName.toLowerCase() === 'input' "
                "&& (element.type || '').toLowerCase() === 'file' "
                "&& element.files.length > 0"
            )
        except Exception:
            return False

    async def submit(self, page) -> SubmissionResult:
        """Attempt submission only when explicitly called; report observed outcome."""
        self._require_supported_page(page)
        await self._check_access_wall(page)
        form = page.locator(self.form_selector)
        if await form.count() != 1:
            return SubmissionResult("uncertain", "application form is missing or ambiguous")
        try:
            # Use the extraction path as the source of truth for unsupported widgets.
            questions = await self.read_questions(page)
        except AdapterDeferred as error:
            return SubmissionResult("uncertain", str(error))
        controls = await form.evaluate(
            """form => Array.from(form.elements).filter(element =>
              element.required || element.getAttribute('aria-required') === 'true'
            ).map(element => {
              const tag = element.tagName.toLowerCase();
              const type = (element.type || '').toLowerCase();
              const multiple = element.multiple === true;
              const supported = !multiple && (tag === 'textarea' || tag === 'select' ||
                (tag === 'input' && ['text', 'email', 'tel', 'url', 'search', 'checkbox', 'file'].includes(type)));
              const kind = type === 'checkbox' ? 'checkbox' :
                (type === 'file' ? 'file' : (tag === 'select' ? 'select' : 'value'));
              return {
                id: element.name || element.id || ('field-' + Array.from(form.elements).indexOf(element)),
                required: true,
                supported,
                valid: element.validity ? element.validity.valid : false,
                file: tag === 'input' && type === 'file',
                fileSelected: tag === 'input' && type === 'file' && element.files.length > 0,
                value: typeof element.value === 'string' ? element.value.trim() : '',
                checked: element.checked === true,
                kind,
                multiple
              };
            })"""
        )
        if (
            not self._required_unknown_questions_are_ready(questions, controls)
            or not self._required_controls_are_ready(controls)
        ):
            return SubmissionResult(
                "uncertain", "required controls are unsupported, invalid, unanswered, or missing a selected file"
            )

        import re

        submit_button = form.get_by_role(
            "button", name=re.compile(r"(submit|apply)", re.I)
        ).and_(form.locator(self.native_submit_selector))
        count = await submit_button.count()
        if count != 1:
            return SubmissionResult("uncertain", "submit control is missing or ambiguous")
        action = (
            await submit_button.get_attribute("formaction")
            or await form.get_attribute("action")
            or page.url
        )
        if not self._is_allowed_url(urljoin(page.url, action)):
            return SubmissionResult("uncertain", "submit target is outside the HTTPS host allowlist")
        blocked_navigation: list[str] = []

        async def guard_main_frame_navigation(route) -> None:
            request = route.request
            frame = request.frame
            if request.is_navigation_request() and frame == frame.page.main_frame:
                if not self._is_allowed_url(request.url):
                    blocked_navigation.append(request.url)
                    await route.abort()
                    return
            await route.fallback()

        try:
            await page.context.route("**/*", guard_main_frame_navigation)
            await submit_button.click()
        except Exception as error:
            return SubmissionResult("uncertain", f"submit outcome could not be observed: {error}")
        finally:
            try:
                await page.context.unroute("**/*", guard_main_frame_navigation)
            except Exception:
                pass

        if blocked_navigation or not self.matches(page):
            return SubmissionResult("uncertain", "off-allowlist main-frame navigation was blocked")

        if await self._has_validation_error(page):
            return SubmissionResult("rejected", "required-field validation rejected the application")
        if await self.confirm_submission(page):
            return SubmissionResult("confirmed", "application confirmation was observed")
        return SubmissionResult("uncertain", "submission confirmation was not observed")

    @staticmethod
    def _required_unknown_questions_are_ready(
        questions: Sequence[FormQuestion], controls: Sequence[dict]
    ) -> bool:
        controls_by_id = {control["id"]: control for control in controls}
        for question in questions:
            if question.required and question.kind == "unknown":
                control = controls_by_id.get(question.id)
                if not control or not control["file"] or not control["fileSelected"]:
                    return False
        return True

    @staticmethod
    def _required_controls_are_ready(controls: Sequence[dict]) -> bool:
        for control in controls:
            if control.get("multiple", False):
                return False
            if not control["supported"] or not control["valid"]:
                return False
            if control["file"] and not control["fileSelected"]:
                return False
            if control["kind"] == "checkbox" and not control["checked"]:
                return False
            if control["kind"] == "unknown" and not (
                control["file"] and control["fileSelected"]
            ):
                return False
            if not control["file"] and control["kind"] != "checkbox" and not control["value"]:
                return False
        return True

    async def _has_validation_error(self, page) -> bool:
        for selector in ("[role='alert']", ".error", "input:invalid", "select:invalid", "textarea:invalid"):
            messages = page.locator(selector)
            for index in range(await messages.count()):
                if await messages.nth(index).is_visible():
                    return True
        return False

    async def confirm_submission(self, page) -> bool:
        self._require_supported_page(page)
        for selector in self.confirmation_selectors:
            confirmation = page.locator(selector)
            if await confirmation.count() == 1 and await confirmation.is_visible():
                text = (await confirmation.inner_text()).strip().casefold()
                if any(word in text for word in ("thank", "success", "submitted", "received")):
                    return True
        body = (await page.locator("body").inner_text()).casefold()
        return any(
            phrase in body
            for phrase in ("thank you for applying", "application submitted", "we received your application")
        )
