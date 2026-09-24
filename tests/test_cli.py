from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobapply.history import ApplicationStatus, HistoryStore
from jobapply import cli


@pytest.fixture
def runner():
    return CliRunner()


class FakeWorkflow:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def run(self, urls, dry_run=False, retry_uncertain=False):
        self.calls.append((urls, dry_run, retry_uncertain))
        return self.outcomes


def test_help_exposes_operational_commands(runner):
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "validate-profile" in result.output
    assert "apply" in result.output
    assert "history" in result.output


def test_apply_parses_utf8_url_file_and_ignores_comments(runner, tmp_path, monkeypatch):
    urls_file = tmp_path / "jobs.txt"
    urls_file.write_text(
        "# curated jobs\n\nhttps://jobs.example/one\n  # skipped\nhttps://jobs.example/two\n",
        encoding="utf-8",
    )
    workflow = FakeWorkflow([
        SimpleNamespace(url="https://jobs.example/one", status=ApplicationStatus.submitted, reason="confirmed"),
        SimpleNamespace(url="https://jobs.example/two", status=ApplicationStatus.submitted, reason="confirmed"),
    ])
    monkeypatch.setattr(cli, "_workflow", lambda: workflow, raising=False)

    result = runner.invoke(cli.app, ["apply", str(urls_file)])

    assert result.exit_code == 0
    assert workflow.calls == [(["https://jobs.example/one", "https://jobs.example/two"], False, False)]


def test_apply_dry_run_reports_without_submission(runner, tmp_path, monkeypatch):
    urls_file = tmp_path / "jobs.txt"
    urls_file.write_text("https://jobs.example/one\n", encoding="utf-8")
    workflow = FakeWorkflow([
        SimpleNamespace(
            url="https://jobs.example/one",
            status=ApplicationStatus.deferred,
            reason="dry run filled; submission skipped",
        )
    ])
    monkeypatch.setattr(cli, "_workflow", lambda: workflow, raising=False)

    result = runner.invoke(cli.app, ["apply", str(urls_file), "--dry-run"])

    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert workflow.calls == [(["https://jobs.example/one"], True, False)]


def test_apply_passes_retry_uncertain_to_workflow(runner, tmp_path, monkeypatch):
    urls_file = tmp_path / "jobs.txt"
    urls_file.write_text("https://jobs.example/one\n", encoding="utf-8")
    workflow = FakeWorkflow([
        SimpleNamespace(
            url="https://jobs.example/one",
            status=ApplicationStatus.submitted,
            reason="confirmed",
        )
    ])
    monkeypatch.setattr(cli, "_workflow", lambda: workflow, raising=False)

    result = runner.invoke(cli.app, ["apply", str(urls_file), "--retry-uncertain"])

    assert result.exit_code == 0
    assert workflow.calls == [(["https://jobs.example/one"], False, True)]


@pytest.mark.parametrize("status", [ApplicationStatus.deferred, ApplicationStatus.failed, ApplicationStatus.uncertain])
def test_apply_returns_nonzero_for_unresolved_outcomes(runner, tmp_path, monkeypatch, status):
    urls_file = tmp_path / "jobs.txt"
    urls_file.write_text("https://jobs.example/private-token\n", encoding="utf-8")
    workflow = FakeWorkflow([
        SimpleNamespace(
            url="https://jobs.example/private-token",
            status=status,
            reason="personal answer sk-test-secret",
        )
    ])
    monkeypatch.setattr(cli, "_workflow", lambda: workflow, raising=False)

    result = runner.invoke(cli.app, ["apply", str(urls_file)])

    assert result.exit_code != 0
    assert status.value in result.output
    assert "personal answer" not in result.output
    assert "sk-test-secret" not in result.output
    assert "private-token" not in result.output


def test_apply_validates_each_url_before_running(runner, tmp_path, monkeypatch):
    urls_file = tmp_path / "jobs.txt"
    urls_file.write_text("https://jobs.example/one\nfile:///private/profile.yaml\n", encoding="utf-8")
    workflow = FakeWorkflow([])
    monkeypatch.setattr(cli, "_workflow", lambda: workflow, raising=False)

    result = runner.invoke(cli.app, ["apply", str(urls_file)])

    assert result.exit_code != 0
    assert workflow.calls == []


def test_validate_profile_reports_validity_without_profile_values(runner, tmp_path, monkeypatch):
    resume = tmp_path / "resume.pdf"
    resume.touch()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "resume_path: resume.pdf\nfull_name: Fictional Person\nemail: private@example.test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROFILE_PATH", str(profile))

    result = runner.invoke(cli.app, ["validate-profile"])

    assert result.exit_code == 0
    assert "valid" in result.output.lower()
    assert "Fictional Person" not in result.output
    assert "private@example.test" not in result.output


def test_history_filters_and_summarizes_without_details(runner, tmp_path, monkeypatch):
    database = tmp_path / "history.sqlite3"
    history = HistoryStore(database)
    for url, status, reason in (
        ("https://jobs.example/one", ApplicationStatus.submitted, "confirmed"),
        ("https://jobs.example/two", ApplicationStatus.deferred, "answer includes private data"),
    ):
        assert history.claim(url)
        history.finish(url, status, {"reason": reason, "personal_answer": "do not print"})
    monkeypatch.setenv("HISTORY_PATH", str(database))

    result = runner.invoke(cli.app, ["history", "--status", "deferred"])

    assert result.exit_code == 0
    assert "deferred" in result.output
    assert "submitted" not in result.output
    assert "private data" not in result.output
    assert "do not print" not in result.output
