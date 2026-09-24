"""Persistent local history used to prevent duplicate applications.

A crash or interrupt mid-run must never silently produce a second application for
the same posting. `claim()` and `finish()` form an atomic two-step: a URL is
claimed into a `processing` state before anything touches the page, and only an
explicit `finish()` call resolves it. A retry of an interrupted `processing` row is
allowed (nothing was confirmed sent); a retry of `submitted` is not.
"""

from contextlib import closing
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class ApplicationStatus(StrEnum):
    """Outcome state recorded for a job application URL.

    `uncertain`: a submit control was clicked but no confirmation was
    observed. Unlike `submitted`, it stays re-claimable.
    """

    processing = "processing"
    submitted = "submitted"
    filled = "filled"
    deferred = "deferred"
    skipped = "skipped"
    uncertain = "uncertain"


_TRACKING_PARAMS = frozenset({"lever-source", "gh_src"})


def _canonicalize(url: str) -> str:
    """Normalize a URL so the same posting is recognized across tracking links."""

    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in _TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class HistoryStore:
    """SQLite-backed, atomic application history for normalized job URLs."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    url TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'processing', 'submitted', 'filled', 'deferred',
                            'skipped', 'uncertain'
                        )
                    ),
                    details TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def claim(self, url: str) -> bool:
        """Atomically claim a URL unless it was already submitted or is in flight.

        Returns False if another run already owns this URL (`processing`) or it was
        already `submitted`. A prior `filled`, `deferred`, `skipped`, or `uncertain`
        outcome may be claimed again, since nothing was confirmed to have happened.
        """

        normalized = _canonicalize(url)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM applications WHERE url = ?", (normalized,)
            ).fetchone()
            if row is not None:
                status = ApplicationStatus(row["status"])
                if status in {ApplicationStatus.processing, ApplicationStatus.submitted}:
                    connection.commit()
                    return False
                connection.execute(
                    """
                    UPDATE applications
                    SET status = ?, details = '{}', updated_at = CURRENT_TIMESTAMP
                    WHERE url = ?
                    """,
                    (ApplicationStatus.processing.value, normalized),
                )
            else:
                connection.execute(
                    "INSERT INTO applications (url, status, details) VALUES (?, ?, '{}')",
                    (normalized, ApplicationStatus.processing.value),
                )
            connection.commit()
            return True

    def finish(self, url: str, status: ApplicationStatus, details: dict | None = None) -> None:
        """Record a final outcome for a URL that is currently `processing`."""

        if status is ApplicationStatus.processing:
            raise ValueError("finish() requires a final application status")
        normalized = _canonicalize(url)
        serialized_details = json.dumps(details or {}, sort_keys=True)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE applications
                SET status = ?, details = ?, updated_at = CURRENT_TIMESTAMP
                WHERE url = ? AND status = ?
                """,
                (
                    status.value,
                    serialized_details,
                    normalized,
                    ApplicationStatus.processing.value,
                ),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                raise ValueError(f"Application URL was not claimed: {normalized}")
            connection.commit()

    def get(self, url: str) -> dict | None:
        """Return stored state for a normalized URL, if present."""

        normalized = _canonicalize(url)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT url, status, details FROM applications WHERE url = ?",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return {
            "url": row["url"],
            "status": row["status"],
            "details": json.loads(row["details"]),
        }

    def list(self, status: ApplicationStatus | None = None) -> list[dict]:
        """Return application records in update order, optionally filtered by status."""

        with closing(self._connect()) as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT url, status, details, updated_at FROM applications "
                    "ORDER BY updated_at DESC, url"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT url, status, details, updated_at FROM applications "
                    "WHERE status = ? ORDER BY updated_at DESC, url",
                    (status.value,),
                ).fetchall()
        return [
            {
                "url": row["url"],
                "status": row["status"],
                "details": json.loads(row["details"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
