"""Tests for the duplicate-application guard. Highest consequence code in the project:
a bug here means a real, duplicate job application."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from artemis.history import ApplicationStatus, HistoryStore


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


def test_claim_new_url_succeeds(store: HistoryStore):
    assert store.claim("https://boards.greenhouse.io/acme/jobs/1") is True
    record = store.get("https://boards.greenhouse.io/acme/jobs/1")
    assert record["status"] == ApplicationStatus.processing.value


def test_claim_twice_second_call_fails(store: HistoryStore):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    assert store.claim(url) is True
    assert store.claim(url) is False


def test_cannot_reclaim_submitted(store: HistoryStore):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    store.claim(url)
    store.finish(url, ApplicationStatus.submitted, {"reason": "sent"})
    assert store.claim(url) is False


def test_can_reclaim_deferred(store: HistoryStore):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    store.claim(url)
    store.finish(url, ApplicationStatus.deferred, {"reason": "unresolved field"})
    assert store.claim(url) is True


def test_can_reclaim_filled(store: HistoryStore):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    store.claim(url)
    store.finish(url, ApplicationStatus.filled, {"reason": "dry run"})
    assert store.claim(url) is True


def test_finish_requires_prior_claim(store: HistoryStore):
    with pytest.raises(ValueError):
        store.finish("https://boards.greenhouse.io/acme/jobs/1", ApplicationStatus.submitted, {})


def test_finish_rejects_processing_status(store: HistoryStore):
    url = "https://boards.greenhouse.io/acme/jobs/1"
    store.claim(url)
    with pytest.raises(ValueError):
        store.finish(url, ApplicationStatus.processing, {})


def test_url_canonicalization_strips_tracking_params(store: HistoryStore):
    store.claim("https://boards.greenhouse.io/acme/jobs/1?utm_source=twitter&gh_src=abc")
    # Same posting via a different tracking link must be recognized as already claimed.
    assert store.claim("https://boards.greenhouse.io/acme/jobs/1?utm_campaign=x") is False


def test_url_canonicalization_preserves_other_query_params(store: HistoryStore):
    store.claim("https://jobs.lever.co/acme/1?ref=friend")
    assert store.claim("https://jobs.lever.co/acme/1") is True


def test_concurrent_claims_only_one_succeeds(tmp_path: Path):
    path = tmp_path / "history.sqlite3"
    HistoryStore(path)  # create schema once
    url = "https://boards.greenhouse.io/acme/jobs/1"

    def try_claim(_):
        return HistoryStore(path).claim(url)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(try_claim, range(8)))

    assert sum(results) == 1


def test_list_filters_by_status(store: HistoryStore):
    store.claim("https://boards.greenhouse.io/acme/jobs/1")
    store.finish("https://boards.greenhouse.io/acme/jobs/1", ApplicationStatus.submitted, {})
    store.claim("https://jobs.lever.co/acme/2")
    store.finish("https://jobs.lever.co/acme/2", ApplicationStatus.deferred, {"reason": "x"})

    submitted = store.list(ApplicationStatus.submitted)
    assert len(submitted) == 1
    assert submitted[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
