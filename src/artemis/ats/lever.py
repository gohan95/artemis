"""Lever job board adapter."""

from artemis.ats.base import BaseATSAdapter


class LeverAdapter(BaseATSAdapter):
    allowed_hosts = ("jobs.lever.co",)
    form_selector = "form"
