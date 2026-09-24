"""Greenhouse job board adapter."""

from artemis.ats.base import BaseATSAdapter


class GreenhouseAdapter(BaseATSAdapter):
    allowed_hosts = ("boards.greenhouse.io", "job-boards.greenhouse.io")
    form_selector = "#application-form, form#application-form, form"
