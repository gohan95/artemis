"""Safe command-line entry points for the job application workflow."""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import typer

from jobapply.history import ApplicationStatus, HistoryStore
from jobapply.profile import load_profile
from jobapply.settings import Settings
from jobapply.workflow import ApplicationWorkflow


app = typer.Typer(no_args_is_help=True, help="Process curated job application URLs.")


def _workflow() -> ApplicationWorkflow:
    return ApplicationWorkflow(settings=Settings.from_env())


def _read_urls(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        typer.echo("Unable to read URLs file as UTF-8.", err=True)
        raise typer.Exit(1)

    urls = []
    for line_number, line in enumerate(lines, start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not any(character.isspace() for character in value)
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            typer.echo(f"Invalid HTTPS URL on line {line_number}.", err=True)
            raise typer.Exit(1)
        urls.append(value)
    if not urls:
        typer.echo("URLs file contains no job URLs.", err=True)
        raise typer.Exit(1)
    return urls


def _summary_reason(status: ApplicationStatus, reason: str, dry_run: bool) -> tuple[str, str]:
    """Map arbitrary workflow text to fixed CLI-safe status and reason labels."""

    if dry_run and status is ApplicationStatus.deferred and reason == "dry run filled; submission skipped":
        return "dry-run", "submission skipped"
    return {
        ApplicationStatus.submitted: ("submitted", "submission confirmed"),
        ApplicationStatus.deferred: ("deferred", "review required"),
        ApplicationStatus.failed: ("failed", "submission failed"),
        ApplicationStatus.uncertain: ("uncertain", "confirmation unavailable"),
        ApplicationStatus.processing: ("processing", "in progress"),
    }[status]


@app.command("validate-profile")
def validate_profile() -> None:
    """Validate the configured profile and its resume file."""

    try:
        load_profile(Settings.from_env().profile_path)
    except Exception as error:
        typer.echo(f"Profile validation failed ({type(error).__name__}).", err=True)
        raise typer.Exit(1)
    typer.echo("Profile is valid; configured resume file exists.")


@app.command()
def apply(
    urls_file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fill forms without submitting."),
    retry_uncertain: bool = typer.Option(False, "--retry-uncertain", help="Retry uncertain prior outcomes."),
) -> None:
    """Process each curated job URL through the application workflow."""

    urls = _read_urls(urls_file)
    try:
        outcomes = asyncio.run(_workflow().run(
            urls, dry_run=dry_run, retry_uncertain=retry_uncertain
        ))
    except Exception as error:
        typer.echo(f"Application run failed ({type(error).__name__}).", err=True)
        raise typer.Exit(1)

    unresolved = False
    for index, outcome in enumerate(outcomes, start=1):
        status = ApplicationStatus(outcome.status)
        display_status, reason = _summary_reason(status, outcome.reason, dry_run)
        typer.echo(f"job {index}: {display_status} — {reason}")
        if status in {
            ApplicationStatus.deferred,
            ApplicationStatus.failed,
            ApplicationStatus.uncertain,
        } and display_status != "dry-run":
            unresolved = True
    if unresolved:
        raise typer.Exit(1)


@app.command()
def history(
    status: ApplicationStatus | None = typer.Option(
        None, "--status", help="Show only records with this status."
    ),
) -> None:
    """Show concise local application history."""

    records = HistoryStore(Settings.from_env().history_path).list(status=status)
    if not records:
        typer.echo("No matching application history.")
        return
    for index, record in enumerate(records, start=1):
        record_status = ApplicationStatus(record["status"])
        _, reason = _summary_reason(record_status, "", False)
        typer.echo(f"job {index}: {record_status.value} — {reason}")
