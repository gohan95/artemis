"""Ashby (jobs.ashbyhq.com) job board adapter.

Ashby's application page has no `<form>` element at all -- controls sit
directly in the page, so `form_selector = None` scopes reads/fills to the
whole page instead of requiring a single form container.

Labels use standard `<label for="...">` association (confirmed against a
real posting), so no custom label-reading strategy is needed beyond the
base adapter's.
"""

from artemis.ats.base import BaseATSAdapter


class AshbyAdapter(BaseATSAdapter):
    allowed_hosts = ("jobs.ashbyhq.com",)
    form_selector = None
    # There is no <form>, so a bare <button> here has no submit semantics of
    # its own; Ashby's submit control is identified only by this stable class
    # (confirmed against a real posting -- the accompanying hashed CSS-module
    # class name is build-specific and not relied on).
    submit_selector = "button.ashby-application-form-submit-button"
