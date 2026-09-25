"""Per-URL orchestration: claim, read, resolve, prompt for gaps, fill, maybe submit.

There is no separate submission gate re-deriving every answer. Confidence is
established once, at resolution time (see `answers.py`): a field is either
resolved from the profile/learned-answers/user input, or it is unresolved and the
run defers on it. Submission additionally requires the caller opted in via
`submit=True` -- staged rollout of autonomy, not a correctness check.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol, Sequence

from artemis.answers import resolve_question
from artemis.answers_store import LearnedAnswers
from artemis.ats.base import ATSAdapter, AdapterDeferred
from artemis.drafting import Draft, DraftAnswer
from artemis.forms import FieldAnswer, FormQuestion
from artemis.history import ApplicationStatus, HistoryStore
from artemis.profile import Profile


class AskUser(Protocol):
    """Ask the user a question live, in whatever UI the caller provides.

    `draft` is an LLM-drafted suggestion to offer as an editable default, or
    None when no drafter is configured or no grounded draft was produced.
    """

    def __call__(self, question: FormQuestion, draft: Draft | None = None) -> str | None: ...


def _is_draftable(question: FormQuestion) -> bool:
    return question.kind in {"text", "unknown"} and not question.options


class OnFilled(Protocol):
    """Called with a filled or submitted page, before it is closed.

    This is the only chance the caller gets to actually look at the page --
    without it, the page closes the instant this URL's outcome is decided,
    which defeats the point of running headed without --submit at all. The
    default implementation returns immediately (unattended/headless/test
    use); a CLI wires this to pause for the person to look.
    """

    async def __call__(self, page, outcome: "ApplicationOutcome") -> None: ...


async def _no_pause(page, outcome: "ApplicationOutcome") -> None:
    return None


@dataclass(frozen=True)
class ApplicationOutcome:
    """A caller-facing result for one normalized application URL."""

    url: str
    status: ApplicationStatus
    reason: str = ""
    filled_fields: tuple[str, ...] = ()
    unresolved_fields: tuple[str, ...] = ()


class ApplicationPipeline:
    """Process a list of URLs once each: fill known fields, prompt for gaps, maybe submit."""

    def __init__(
        self,
        *,
        profile: Profile,
        history: HistoryStore,
        learned: LearnedAnswers,
        adapters: Sequence[ATSAdapter],
        page_factory: Callable,
        ask_user: AskUser,
        on_filled: OnFilled = _no_pause,
        draft_answer: DraftAnswer | None = None,
    ):
        self.profile = profile
        self.history = history
        self.learned = learned
        self.adapters = list(adapters)
        self.page_factory = page_factory
        self.ask_user = ask_user
        self.on_filled = on_filled
        self.draft_answer = draft_answer

    async def run(self, urls: list[str], *, submit: bool) -> list[ApplicationOutcome]:
        return [await self._process_url(url, submit=submit) for url in urls]

    async def _process_url(self, url: str, *, submit: bool) -> ApplicationOutcome:
        if not self.history.claim(url):
            existing = self.history.get(url) or {}
            return ApplicationOutcome(
                url=existing.get("url", url),
                status=ApplicationStatus(existing.get("status", ApplicationStatus.skipped)),
                reason="already processed",
            )
        canonical = self.history.get(url)["url"]

        adapter = next((candidate for candidate in self.adapters if candidate.supports_url(url)), None)
        if adapter is None:
            return self._finish(canonical, ApplicationStatus.deferred, "unsupported ATS")

        page = None
        outcome: ApplicationOutcome | None = None
        try:
            page = await self._open(url)
            if not adapter.matches(page):
                outcome = self._finish(canonical, ApplicationStatus.deferred, "unsupported ATS")
                return outcome

            # read_questions waits for the page's real content (and any
            # CAPTCHA/login wall) to actually exist before looking at it --
            # see BaseATSAdapter._require_form_scope.
            questions = await adapter.read_questions(page)
            answers, unresolved, sensitive_gaps = self._resolve_all(questions)

            for question in sensitive_gaps:
                # Never prompt or fill a sensitive question with no explicit
                # profile value -- surfaced to the user as a defer reason, not
                # asked for live, so an answer can't slip in unrecorded.
                unresolved.append(question)

            still_unresolved: list[FormQuestion] = []
            for question in unresolved:
                if question in sensitive_gaps:
                    still_unresolved.append(question)
                    continue
                draft = None
                if self.draft_answer is not None and _is_draftable(question):
                    draft = self.draft_answer(question)
                value = self.ask_user(question, draft)
                if value is None or not value.strip():
                    still_unresolved.append(question)
                    continue
                self.learned.set(question.label, value)
                resolution = resolve_question(question, self.profile, self.learned)
                if resolution.answer is not None:
                    answers.append(resolution.answer)
                else:
                    still_unresolved.append(question)

            await adapter.fill(page, answers)

            filled_ids = tuple(answer.question_id for answer in answers)
            unresolved_ids = tuple(question.id for question in still_unresolved)

            # An unresolved OPTIONAL field is not a reason to stop: "optional"
            # means the form itself does not require an answer, so leaving it
            # blank is a valid, complete application. A required field, or a
            # sensitive one, must still defer the whole run -- submitting with
            # either missing would be an invalid application, not a smaller
            # one. A sensitive gap blocks regardless of the page's own
            # `required` flag: some ATS forms don't mark work-authorization
            # etc. as HTML-required even though skipping it is never
            # acceptable, and that must not depend on the page's own markup.
            blocking_unresolved = [
                q for q in still_unresolved if q.required or q in sensitive_gaps
            ]
            if blocking_unresolved:
                outcome = self._finish(
                    canonical, ApplicationStatus.deferred, "unresolved required fields",
                    filled_ids, unresolved_ids,
                )
                return outcome

            reason = "filled; submission not requested (pass --submit to send)"
            if still_unresolved:
                reason += "; optional fields left blank, review before submitting"
            if not submit:
                outcome = self._finish(
                    canonical, ApplicationStatus.filled, reason, filled_ids, unresolved_ids,
                )
                return outcome

            result = await adapter.submit(page)
            if result.status == "uncertain":
                # A click happened but wasn't confirmed -- distinct from
                # both `deferred` and `submitted`, and re-claimable.
                outcome = self._finish(
                    canonical, ApplicationStatus.uncertain, result.reason, filled_ids, unresolved_ids
                )
                return outcome
            if result.status != "confirmed":
                outcome = self._finish(canonical, ApplicationStatus.deferred, result.reason, filled_ids, unresolved_ids)
                return outcome
            outcome = self._finish(
                canonical, ApplicationStatus.submitted,
                f"submitted at {datetime.now(UTC).isoformat()}",
                filled_ids, unresolved_ids,
            )
            return outcome
        except AdapterDeferred as error:
            outcome = self._finish(canonical, ApplicationStatus.deferred, str(error))
            return outcome
        except Exception as error:
            outcome = self._finish(canonical, ApplicationStatus.deferred, f"{type(error).__name__}: {error}")
            return outcome
        finally:
            if page is not None:
                # Give the caller a chance to look at (or capture a receipt
                # of) the page before it closes. Nothing to look at for an
                # early defer: the page never got past read_questions.
                if outcome is not None and outcome.status in {
                    ApplicationStatus.filled,
                    ApplicationStatus.submitted,
                    ApplicationStatus.uncertain,
                }:
                    try:
                        await self.on_filled(page, outcome)
                    except Exception:
                        pass
                if hasattr(page, "close"):
                    try:
                        await page.close()
                    except Exception:
                        pass

    async def _open(self, url: str):
        page = self.page_factory(url)
        if hasattr(page, "__await__"):
            page = await page
        if getattr(page, "url", None) != url:
            await page.goto(url, wait_until="domcontentloaded")
        return page

    def _resolve_all(
        self, questions: list[FormQuestion]
    ) -> tuple[list[FieldAnswer], list[FormQuestion], list[FormQuestion]]:
        answers: list[FieldAnswer] = []
        unresolved: list[FormQuestion] = []
        sensitive_gaps: list[FormQuestion] = []
        for question in questions:
            resolution = resolve_question(question, self.profile, self.learned)
            if resolution.answer is not None:
                answers.append(resolution.answer)
            elif resolution.sensitive_unanswered:
                sensitive_gaps.append(question)
            else:
                unresolved.append(question)
        return answers, unresolved, sensitive_gaps

    def _finish(
        self,
        url: str,
        status: ApplicationStatus,
        reason: str,
        filled_ids: tuple[str, ...] = (),
        unresolved_ids: tuple[str, ...] = (),
    ) -> ApplicationOutcome:
        self.history.finish(
            url,
            status,
            {
                "reason": reason,
                "filled_fields": list(filled_ids),
                "unresolved_fields": list(unresolved_ids),
            },
        )
        return ApplicationOutcome(
            url=url, status=status, reason=reason,
            filled_fields=filled_ids, unresolved_fields=unresolved_ids,
        )
