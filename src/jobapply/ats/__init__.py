"""Supported applicant tracking system browser adapters."""

from jobapply.ats.base import AdapterDeferred, ATSAdapter, SubmissionResult
from jobapply.ats.greenhouse import GreenhouseAdapter
from jobapply.ats.lever import LeverAdapter

__all__ = [
    "ATSAdapter",
    "AdapterDeferred",
    "GreenhouseAdapter",
    "LeverAdapter",
    "SubmissionResult",
]
