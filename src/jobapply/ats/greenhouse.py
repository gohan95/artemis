"""Greenhouse application form adapter."""

from jobapply.ats.base import BaseATSAdapter


class GreenhouseAdapter(BaseATSAdapter):
    """Adapter for boards hosted by Greenhouse."""

    allowed_hosts = ("greenhouse.io", "greenhouse.com")
    form_selector = "#application-form, form"
    context_selectors = (
        "#job-description",
        ".job__description",
        "[data-qa='job-description']",
        "main",
    )
    confirmation_selectors = (
        "#confirmation",
        "[data-qa='application-confirmation']",
        "[role='status']",
    )
