"""Ashby (jobs.ashbyhq.com) job board adapter.

Ashby's application page has no `<form>` element at all -- controls sit
directly in the page, so `form_selector = None` scopes reads/fills to the
whole page instead of requiring a single form container.

Most fields use standard `<label for="...">` association with `for` matching
the control's `id` (confirmed against a real posting). Ashby's Yes/No widget
is the exception: the underlying checkbox has a `name` but no `id`, so a
`label[for]` lookup by `id` never matches it and the base adapter falls back
to the raw UUID `name` as the "label". `_label_for_control` below reads the
label from the field's wrapper instead, which works regardless of whether the
control has an `id`.

That same Yes/No widget's checkbox is also not the clickable element -- it is
a hidden (`tabindex="-1"`, zero-size) proxy for form state; the real UI is a
sibling `<button data-option="yes|no">`. `_fill_hidden_checkbox` below clicks
that button instead of trying to check the invisible input directly.
"""

from artemis.ats.base import BaseATSAdapter

_FIELD_ENTRY_SELECTOR = ".ashby-application-form-field-entry"
_QUESTION_TITLE_SELECTOR = ".ashby-application-form-question-title"
_YESNO_OPTION_SELECTOR = "[data-option]"


class AshbyAdapter(BaseATSAdapter):
    allowed_hosts = ("jobs.ashbyhq.com",)
    form_selector = None
    # There is no <form>, so a bare <button> here has no submit semantics of
    # its own; Ashby's submit control is identified only by this stable class
    # (confirmed against a real posting -- the accompanying hashed CSS-module
    # class name is build-specific and not relied on).
    submit_selector = "button.ashby-application-form-submit-button"

    async def _label_for_control(self, control) -> str | None:
        label = await control.evaluate(
            f"""element => {{
              const entry = element.closest({_FIELD_ENTRY_SELECTOR!r});
              const title = entry && entry.querySelector({_QUESTION_TITLE_SELECTOR!r});
              return title ? title.innerText.trim() : '';
            }}"""
        )
        return label or None

    async def _fill_hidden_checkbox(self, control, checked: bool) -> bool:
        option = "yes" if checked else "no"
        button = control.locator("xpath=..").locator(f"{_YESNO_OPTION_SELECTOR}[data-option={option!r}]")
        if await button.count() != 1:
            return False
        await self.pacer.before_click(button)
        await button.click()
        return True
