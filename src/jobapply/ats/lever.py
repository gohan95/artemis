"""Lever application form adapter."""

from jobapply.ats.base import BaseATSAdapter


class LeverAdapter(BaseATSAdapter):
    """Adapter for jobs hosted by Lever."""

    allowed_hosts = ("jobs.lever.co",)
    form_selector = "form.application-form, form"
    context_selectors = (
        ".posting-description",
        ".posting-page",
        "[data-qa='job-description']",
    )
    confirmation_selectors = (
        ".application-confirmation",
        "[data-qa='application-confirmation']",
        "[role='status']",
    )
