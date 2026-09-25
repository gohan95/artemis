"""Artemis CLI: process curated job application URLs."""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import typer
import yaml

from artemis.answers_store import LearnedAnswers
from artemis.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from artemis.drafting import Draft, GroundedDrafter
from artemis.forms import FormQuestion
from artemis.history import ApplicationStatus, HistoryStore
from artemis.llm import build_client
from artemis.pipeline import ApplicationPipeline
from artemis.profile import load_profile
from artemis.settings import Settings

app = typer.Typer(add_completion=False)


def _read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlsplit(line)
        if parsed.scheme != "https" or parsed.username or parsed.password or any(
            char.isspace() for char in line
        ):
            typer.echo(f"Refusing unsupported URL: {line}", err=True)
            raise typer.Exit(code=1)
        urls.append(line)
    return urls


def _prompt_user(question: FormQuestion, draft: Draft | None = None) -> str | None:
    prompt = f"[unresolved] {question.label}"
    if question.options:
        prompt += f" (options: {', '.join(question.options)})"
    if draft is not None:
        typer.echo(f"  drafted from: {', '.join(draft.evidence_ids)}")
    if question.required:
        prompt += " [blank to defer this application]: "
    else:
        prompt += " [blank to skip]: "
    try:
        value = typer.prompt(prompt, default=draft.text if draft else "", show_default=bool(draft))
    except (EOFError, KeyboardInterrupt):
        return None
    return value or None


async def _pause_for_review(page, outcome) -> None:
    """Hold a filled/submitted page open until the person says to move on.

    Without this, the page (and the visible browser window showing it)
    closes the instant its outcome is decided -- which, when running headed
    without --submit, defeats the entire point of stopping short of
    submission: there would be nothing left to look at by the time the CLI
    prints its summary line.
    """
    typer.echo(f"  -> {outcome.url}: {outcome.status.value}, browser open for review.")
    try:
        await asyncio.to_thread(typer.prompt, "  Press Enter to close this page and continue", default="", show_default=False)
    except (EOFError, KeyboardInterrupt):
        pass


async def _combined_on_filled(page, outcome, *, receipts_root: Path, pause: bool) -> None:
    """Compose receipt capture with the optional headed review pause, so
    both fire from the single `on_filled` slot the pipeline exposes."""

    from artemis.receipts import capture_receipt

    await capture_receipt(page, outcome, receipts_root=receipts_root)
    if pause:
        await _pause_for_review(page, outcome)


@app.command()
def apply(
    urls_file: Path = typer.Argument(..., help="Path to a file of one https job URL per line."),
    submit: bool = typer.Option(
        False, "--submit", help="Actually submit filled applications. Defaults to off."
    ),
    headless: bool = typer.Option(
        None,
        help="Run the browser without a visible window. Headless Chrome is "
        "detectable regardless of launch flags, so this trades stealth for "
        "unattended runs. Defaults to the ARTEMIS_BROWSER_HEADLESS setting.",
    ),
    pacing: bool = typer.Option(
        None,
        "--pacing/--no-pacing",
        help="Human-paced typing, think-time, and mouse movement instead of "
        "instant fills. Defaults to the ARTEMIS_PACING_ENABLED setting.",
    ),
    profile_dir: Path = typer.Option(
        None, help="Persistent browser profile directory."
    ),
    draft: bool = typer.Option(
        True,
        "--draft/--no-draft",
        help="Offer an LLM-drafted suggestion for unresolved free-text questions "
        "when ARTEMIS_LLM_API_KEY is set. Has no effect otherwise.",
    ),
):
    """Fill (and, with --submit, send) applications for each URL in URLS_FILE."""

    settings = Settings.from_env()
    profile = load_profile(settings.profile_path)
    history = HistoryStore(settings.history_path)
    learned = LearnedAnswers(settings.learned_answers_path)
    urls = _read_urls(urls_file)

    draft_answer = None
    if draft:
        client = build_client(settings)
        if client is not None:
            draft_answer = GroundedDrafter(client, profile)

    resolved_headless = settings.browser_headless if headless is None else headless
    resolved_pacing = settings.pacing_enabled if pacing is None else pacing
    resolved_profile_dir = profile_dir or settings.browser_profile_dir

    async def run() -> None:
        from artemis.browser import BrowserOptions, open_session, page_factory_for
        from artemis.pacing import HumanPacer, NullPacer

        pacer = (
            HumanPacer(settings.pacing_profile(), seed=settings.pacing_seed)
            if resolved_pacing
            else NullPacer()
        )

        options = BrowserOptions(
            profile_dir=resolved_profile_dir,
            headless=resolved_headless,
            channel=settings.browser_channel,
            proxy=_proxy_config(settings),
        )

        async with open_session(options) as session:

            async def on_filled(page, outcome):
                await _combined_on_filled(
                    page,
                    outcome,
                    receipts_root=settings.receipts_dir,
                    pause=not resolved_headless,
                )

            pipeline = ApplicationPipeline(
                profile=profile,
                history=history,
                learned=learned,
                adapters=[
                    GreenhouseAdapter(pacer=pacer),
                    LeverAdapter(pacer=pacer),
                    AshbyAdapter(pacer=pacer),
                ],
                page_factory=page_factory_for(session),
                ask_user=_prompt_user,
                on_filled=on_filled,
                draft_answer=draft_answer,
            )
            outcomes = await pipeline.run(urls, submit=submit)

        exit_code = 0
        for outcome in outcomes:
            typer.echo(f"{outcome.url}: {outcome.status.value} -- {outcome.reason}")
            if outcome.unresolved_fields:
                typer.echo(f"  unresolved: {', '.join(outcome.unresolved_fields)}")
            if outcome.status not in {ApplicationStatus.submitted, ApplicationStatus.filled}:
                exit_code = 1
        raise typer.Exit(code=exit_code)

    asyncio.run(run())


def _proxy_config(settings: Settings):
    from artemis.browser import ProxyConfig

    proxy = settings.proxy()
    if proxy is None:
        return None
    return ProxyConfig(
        server=proxy["server"],
        username=proxy.get("username"),
        password=proxy.get("password"),
    )


@app.command()
def history(status: str | None = typer.Option(None, help="Filter by status.")):
    """List locally recorded application outcomes."""

    settings = Settings.from_env()
    store = HistoryStore(settings.history_path)
    filter_status = ApplicationStatus(status) if status else None
    for record in store.list(filter_status):
        reason = record["details"].get("reason", "")
        typer.echo(f"{record['updated_at']}  {record['status']:<10}  {record['url']}  {reason}")


@app.command("validate-profile")
def validate_profile():
    """Load and validate the configured profile file."""

    settings = Settings.from_env()
    try:
        profile = load_profile(settings.profile_path)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Profile is valid. Resume: {profile.resume_path}")


@app.command()
def setup(
    resume: Path | None = typer.Option(None, help="Resume to auto-populate from."),
    no_resume: bool = typer.Option(
        False, "--no-resume", help="Skip resume parsing; enter everything by hand."
    ),
):
    """Interactively create or edit the profile, optionally auto-populated from a resume."""

    from artemis.profile_setup import (
        extract_profile_fields,
        merge_profile,
        resume_text,
        write_profile,
    )

    settings = Settings.from_env()
    try:
        existing = load_profile(settings.profile_path)
    except FileNotFoundError:
        existing = None
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1)

    resume_path = resume or (existing.resume_path if existing else None)
    extracted: dict = {}
    if not no_resume and resume_path is not None:
        client = build_client(settings)
        if client is None:
            typer.echo("No ARTEMIS_LLM_API_KEY configured -- skipping resume auto-population.")
        else:
            try:
                text = resume_text(resume_path)
                extracted = extract_profile_fields(text, client)
            except ValueError as error:
                typer.echo(f"{error} -- continuing with manual entry.")
            if not extracted:
                typer.echo("Could not auto-populate from the resume -- continuing with manual entry.")

    def ask(prompt_text: str, default: str | None) -> str | None:
        value = typer.prompt(prompt_text, default=default or "", show_default=bool(default))
        return value or None

    def prior(field: str) -> str | None:
        return extracted.get(field) or (getattr(existing, field, None) if existing else None)

    answers: dict = {
        "full_name": ask("Full name", prior("full_name")),
        "email": ask("Email", prior("email")),
        "phone": ask("Phone", prior("phone")),
        "location": ask("Location", prior("location")),
        "website": ask("Website", prior("website")),
        "linkedin": ask("LinkedIn", prior("linkedin")),
        "background": ask(
            "Background (a sentence or two on what you're looking for)", prior("background")
        ),
    }
    if resume_path is not None:
        answers["resume_path"] = str(resume_path)

    profile = merge_profile(existing, extracted, answers)

    typer.echo("\nAssembled profile:")
    typer.echo(yaml.safe_dump(profile.model_dump(mode="json", exclude_none=True), sort_keys=False))
    typer.echo(
        "(Work history and education came from the resume, if any; hand-edit "
        f"{settings.profile_path} afterward for anything auto-population missed.)"
    )
    if not typer.confirm(f"Write this to {settings.profile_path}?", default=True):
        typer.echo("Not written.")
        raise typer.Exit(code=0)

    write_profile(profile, settings.profile_path)
    typer.echo(f"Wrote {settings.profile_path}.")


if __name__ == "__main__":
    app()
