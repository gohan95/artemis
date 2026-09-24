from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from jobapply.history import ApplicationStatus, HistoryStore


@pytest.fixture
def history(tmp_path):
    return HistoryStore(tmp_path / "nested" / "history.sqlite3")


def test_first_claim_creates_processing_history(history):
    assert history.claim("https://jobs.example/role/123") is True
    assert history.get("https://jobs.example/role/123") == {
        "url": "https://jobs.example/role/123",
        "status": "processing",
        "details": {},
    }


def test_current_processing_url_cannot_be_claimed_twice(history):
    url = "https://jobs.example/role/123"
    assert history.claim(url) is True
    assert history.claim(url) is False


def test_submitted_url_cannot_be_claimed_twice(history):
    assert history.claim("https://jobs.example/role/123?utm_source=x") is True
    history.finish(
        "https://jobs.example/role/123", ApplicationStatus.submitted, {}
    )
    assert history.claim("https://jobs.example/role/123") is False


def test_url_identity_removes_fragments_and_known_tracking_parameters(history):
    url = "https://jobs.example/role/123?team=platform&utm_source=x&lever-source=board&gh_src=feed#apply"
    normalized = "https://jobs.example/role/123?team=platform"

    assert history.claim(url) is True
    assert history.get(normalized) == {
        "url": normalized,
        "status": "processing",
        "details": {},
    }
    assert history.claim(normalized) is False


def test_url_identity_preserves_unknown_query_parameters(history):
    assert history.claim("https://jobs.example/role/123?job_id=456&utm_medium=email")
    assert history.claim("https://jobs.example/role/123?job_id=789")


@pytest.mark.parametrize(
    ("status", "retry", "expected"),
    [
        (ApplicationStatus.submitted, False, False),
        (ApplicationStatus.deferred, False, True),
        (ApplicationStatus.failed, False, True),
        (ApplicationStatus.uncertain, False, False),
        (ApplicationStatus.uncertain, True, True),
    ],
)
def test_claim_retry_policy(history, status, retry, expected):
    url = "https://jobs.example/role/123"
    assert history.claim(url)
    history.finish(url, status, {"reason": "recorded"})

    assert history.claim(url, retry_uncertain=retry) is expected


def test_finish_persists_status_and_details(history):
    url = "https://jobs.example/role/123"
    details = {"confirmation": "Application received", "attempt": 1}
    assert history.claim(url)

    history.finish(url, ApplicationStatus.submitted, details)

    assert history.get(url) == {
        "url": url,
        "status": "submitted",
        "details": details,
    }


def test_finish_cannot_overwrite_a_terminal_outcome(history):
    url = "https://jobs.example/role/123"
    assert history.claim(url)
    history.finish(url, ApplicationStatus.submitted, {"attempt": 1})

    with pytest.raises(ValueError, match="not claimed"):
        history.finish(url, ApplicationStatus.failed, {"attempt": 2})

    assert history.get(url) == {
        "url": url,
        "status": "submitted",
        "details": {"attempt": 1},
    }


def test_finish_does_not_create_an_identity(history):
    with pytest.raises(ValueError, match="not claimed"):
        history.finish(
            "https://jobs.example/unknown", ApplicationStatus.failed, {}
        )
    assert history.get("https://jobs.example/unknown") is None


def test_claim_reclaims_deferred_or_failed_row(history):
    url = "https://jobs.example/role/123"
    assert history.claim(url)
    history.finish(url, ApplicationStatus.deferred, {"reason": "missing answer"})

    assert history.claim(url)
    assert history.get(url) == {"url": url, "status": "processing", "details": {}}


def test_concurrent_claims_for_same_url_only_succeed_once(tmp_path):
    database = tmp_path / "history.sqlite3"
    url = "https://jobs.example/role/123"

    def claim():
        return HistoryStore(database).claim(url)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim(), range(8)))

    assert results.count(True) == 1


def test_get_returns_none_for_unknown_url(history):
    assert history.get("https://jobs.example/unknown") is None


def test_list_filters_records_by_status(history):
    for url, status in (
        ("https://jobs.example/one", ApplicationStatus.submitted),
        ("https://jobs.example/two", ApplicationStatus.deferred),
        ("https://jobs.example/three", ApplicationStatus.deferred),
    ):
        assert history.claim(url)
        history.finish(url, status, {"reason": status.value})

    records = history.list(status=ApplicationStatus.deferred)

    assert [record["url"] for record in records] == [
        "https://jobs.example/three",
        "https://jobs.example/two",
    ]
    assert all(record["status"] == "deferred" for record in records)


def test_list_orders_newest_first_and_breaks_timestamp_ties_by_url(history):
    urls = [
        "https://jobs.example/older",
        "https://jobs.example/tie-b",
        "https://jobs.example/newest",
        "https://jobs.example/tie-a",
    ]
    for url in urls:
        assert history.claim(url)
        history.finish(url, ApplicationStatus.submitted, {})
    with sqlite3.connect(history.path) as connection:
        connection.executemany(
            "UPDATE applications SET updated_at = ? WHERE url = ?",
            [
                ("2026-09-20 10:00:00", urls[0]),
                ("2026-09-22 10:00:00", urls[1]),
                ("2026-09-23 10:00:00", urls[2]),
                ("2026-09-22 10:00:00", urls[3]),
            ],
        )

    records = history.list()

    assert [record["url"] for record in records] == [
        "https://jobs.example/newest",
        "https://jobs.example/tie-a",
        "https://jobs.example/tie-b",
        "https://jobs.example/older",
    ]
    assert [record["updated_at"] for record in records] == [
        "2026-09-23 10:00:00",
        "2026-09-22 10:00:00",
        "2026-09-22 10:00:00",
        "2026-09-20 10:00:00",
    ]
