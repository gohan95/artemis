"""Coordinate safe application processing and persistent status transitions."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Sequence

from jobapply.ats import GreenhouseAdapter, LeverAdapter
from jobapply.forms import FieldAnswer, FormQuestion
from jobapply.generation import TextGenerator
from jobapply.history import ApplicationStatus, HistoryStore
from jobapply.jev import JevClient
from jobapply.mapping import (
    _is_known_question,
    _is_resume_question,
    _question_profile_value,
    is_sensitive_question,
    map_known_question,
)
from jobapply.policy import submission_decision, validate_and_render_answer
from jobapply.profile import EvidenceFact, Profile, load_evidence, load_profile
from jobapply.settings import Settings


@dataclass(frozen=True)
class ApplicationOutcome:
    """A caller-facing result for one normalized application URL."""

    url: str
    status: ApplicationStatus
    employer: str | None = None
    role: str | None = None
    submitted_at: str | None = None
    reason: str = ""


class ApplicationWorkflow:
    """Process URLs once through mapping, policy, filling, and explicit submit."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        profile: Profile | None = None,
        evidence: Sequence[EvidenceFact] | None = None,
        history: HistoryStore | None = None,
        adapters=None,
        page_factory: Callable | None = None,
        jev_client=None,
        generator=None,
    ):
        self.settings = settings or Settings.from_env()
        self.profile = profile or load_profile(self.settings.profile_path)
        self.evidence = list(evidence) if evidence is not None else load_evidence(self.profile)
        self.history = history or HistoryStore(self.settings.history_path)
        self.adapters = list(adapters) if adapters is not None else [GreenhouseAdapter(), LeverAdapter()]
        self.page_factory = page_factory
        self.jev_client = jev_client or JevClient(self.settings)
        self.generator = generator or TextGenerator(self.settings)

    async def run(
        self, urls: list[str], dry_run: bool = False, retry_uncertain: bool = False
    ) -> list[ApplicationOutcome]:
        if self.page_factory is not None:
            return [
                await self._process_url(url, dry_run, retry_uncertain, self.page_factory)
                for url in urls
            ]

        claimed = [self.history.claim(url, retry_uncertain=retry_uncertain) for url in urls]
        outcomes: list[ApplicationOutcome | None] = [
            None if success else self._existing_outcome(url)
            for url, success in zip(urls, claimed)
        ]
        if not any(claimed):
            return outcomes

        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
        except Exception as error:
            return self._finish_claimed_urls(
                urls, claimed, outcomes, f"browser startup failed: {type(error).__name__}"
            )

        browser = None
        try:
            try:
                browser = await playwright.chromium.launch(headless=self.settings.browser_headless)
            except Exception as error:
                return self._finish_claimed_urls(
                    urls, claimed, outcomes, f"browser launch failed: {type(error).__name__}"
                )

            async def page_factory(url):
                return await browser.new_page()

            for index, (url, success) in enumerate(zip(urls, claimed)):
                if success:
                    outcomes[index] = await self._process_url(
                        url, dry_run, retry_uncertain, page_factory, already_claimed=True
                    )
            return self._refresh_unclaimed_outcomes(urls, claimed, outcomes)
        finally:
            if browser is not None:
                await browser.close()
            await playwright.stop()

    async def _process_url(self, url, dry_run, retry_uncertain, page_factory, already_claimed=False):
        if not already_claimed and not self.history.claim(url, retry_uncertain=retry_uncertain):
            return self._existing_outcome(url)
        canonical = self.history.get(url)["url"]
        page = None
        submit_started = False
        try:
            adapter = next(
                (candidate for candidate in self.adapters if candidate.supports_url(url)), None
            )
            if adapter is None:
                return self._finish(canonical, ApplicationStatus.deferred, "unsupported ATS")
            page = page_factory(url)
            if hasattr(page, "__await__"):
                page = await page
            if getattr(page, "url", None) != url:
                await self._navigate_to_supported_url(page, adapter, url)
            if not adapter.matches(page):
                return self._finish(canonical, ApplicationStatus.deferred, "unsupported ATS")

            questions = await adapter.read_questions(page)
            context = await adapter.read_job_context(page)
            answers: list[FieldAnswer] = []
            validated_generated_ids: set[str] = set()
            unresolved: list[FormQuestion] = []
            resume_questions: list[FormQuestion] = []

            for question in questions:
                if _is_resume_question(question):
                    if question.required:
                        if not self.profile.resume_path.is_file():
                            return self._finish(canonical, ApplicationStatus.deferred, "required resume is missing")
                        resume_questions.append(question)
                        answers.append(
                            FieldAnswer(question.id, str(self.profile.resume_path), ["profile.resume_path"], "profile")
                        )
                    continue

                mapped = map_known_question(question, self.profile)
                if mapped is not None:
                    answers.append(mapped)
                    continue

                if is_sensitive_question(question):
                    return self._finish(
                        canonical, ApplicationStatus.deferred, "sensitive_answer_missing"
                    )

                resolved = _question_profile_value(question, self.profile)
                # Sensitive fields are never delegated to Jev or generated text.
                if resolved is not None and resolved[2]:
                    unresolved.append(question)
                    continue
                unresolved.append(question)

            decisions = self._decide_structured_questions(unresolved)
            still_unresolved: list[FormQuestion] = []
            for question in unresolved:
                if question.id in decisions:
                    answer = decisions[question.id]
                    if answer is not None:
                        answers.append(answer)
                    else:
                        still_unresolved.append(question)
                else:
                    still_unresolved.append(question)

            for question in still_unresolved:
                # A Jev route to free text only enables generation for text fields.
                if question.kind != "text" or not context:
                    continue
                resolved = _question_profile_value(question, self.profile)
                if resolved is not None and resolved[2]:
                    continue
                safe_evidence = [
                    fact for fact in self.evidence
                    if not fact.id.startswith("profile.sensitive_answers.")
                ]
                try:
                    draft = self.generator.draft(question, safe_evidence, context)
                    decision, rendered = validate_and_render_answer(
                        draft, safe_evidence, question=question, jev_client=self.jev_client
                    )
                except Exception:
                    continue
                if decision.action == "submit" and rendered is not None:
                    evidence_ids = [
                        identifier
                        for claim in draft.claims
                        for identifier in claim.evidence_ids
                    ]
                    answers.append(FieldAnswer(question.id, rendered, list(dict.fromkeys(evidence_ids)), "generated"))
                    validated_generated_ids.add(question.id)

            # Check every ordinary question before touching the form. Required
            # resume controls are checked after fill, because only the adapter
            # can establish that the local file was selected successfully.
            gate = submission_decision(
                answers,
                [
                    question for question in questions
                    if question not in resume_questions and question.id not in validated_generated_ids
                ],
                self.profile,
            )
            if gate.action == "defer":
                return self._finish(
                    canonical, ApplicationStatus.deferred, ", ".join(gate.reason_codes)
                )

            await adapter.fill(page, answers)
            for question in resume_questions:
                verifier = getattr(adapter, "verify_resume_upload", None)
                try:
                    verified = verifier is not None and await verifier(page, question.id)
                except Exception:
                    verified = False
                if not verified:
                    return self._finish(
                        canonical, ApplicationStatus.deferred,
                        f"required resume upload could not be verified: {question.id}",
                    )
            gate = submission_decision(
                answers,
                [
                    question for question in questions
                    if question not in resume_questions and question.id not in validated_generated_ids
                ],
                self.profile,
            )
            if gate.action == "defer":
                return self._finish(
                    canonical, ApplicationStatus.deferred, ", ".join(gate.reason_codes)
                )
            if dry_run:
                return self._finish(canonical, ApplicationStatus.deferred, "dry run filled; submission skipped")

            submit_started = True
            result = await adapter.submit(page)
            if result.status == "rejected":
                return self._finish(canonical, ApplicationStatus.failed, result.reason)
            if result.status != "confirmed":
                return self._finish(canonical, ApplicationStatus.uncertain, result.reason)
            try:
                confirmed = await adapter.confirm_submission(page)
            except Exception as error:
                return self._finish(
                    canonical, ApplicationStatus.uncertain,
                    f"submission confirmation failed: {type(error).__name__}",
                )
            if not confirmed:
                return self._finish(canonical, ApplicationStatus.uncertain, "submission confirmation not established")
            details = {"reason": result.reason, "submitted_at": datetime.now(UTC).isoformat()}
            return self._finish(canonical, ApplicationStatus.submitted, result.reason, details)
        except Exception as error:
            status = ApplicationStatus.uncertain if submit_started else ApplicationStatus.deferred
            reason = f"{type(error).__name__}: {error}"
            return self._finish(canonical, status, reason)
        finally:
            if page is not None and hasattr(page, "close"):
                try:
                    await page.close()
                except Exception:
                    pass

    async def _navigate_to_supported_url(self, page, adapter, url):
        """Navigate only while an exact-host main-frame guard is active."""
        blocked_urls: list[str] = []

        async def guard_main_frame_navigation(route) -> None:
            request = route.request
            frame = request.frame
            if request.is_navigation_request() and frame == frame.page.main_frame:
                if not adapter.supports_url(request.url):
                    blocked_urls.append(request.url)
                    await route.abort()
                    return
            await route.fallback()

        can_guard_navigation = hasattr(page, "route") and hasattr(page, "unroute")
        if can_guard_navigation:
            await page.route("**/*", guard_main_frame_navigation)
        try:
            await page.goto(url, wait_until="domcontentloaded")
        finally:
            if can_guard_navigation:
                await page.unroute("**/*", guard_main_frame_navigation)
        if blocked_urls:
            raise RuntimeError("off-allowlist main-frame navigation was blocked")

    def _decide_structured_questions(self, questions):
        unresolved = [
            q for q in questions
            if q.kind == "select"
            and not _is_known_question(q)
        ]
        if not unresolved:
            return {}
        safe_facts = [
            fact for fact in self.evidence
            if not fact.id.startswith("profile.sensitive_answers.")
        ]
        candidates = {
            question.id: [
                fact for fact in safe_facts
                if any(
                    option.strip().casefold() == fact.value.strip().casefold()
                    for option in question.options
                )
            ]
            for question in unresolved
        }
        jev_questions = {
            question.id: {
                "text": question.label,
                "options": list(dict.fromkeys(
                    [fact.id for fact in candidates[question.id]] + ["free_text", "defer"]
                )),
            }
            for question in unresolved
        }
        candidate_ids = {fact.id for facts in candidates.values() for fact in facts}
        state = {"facts": [
            {"id": fact.id, "value": fact.value}
            for fact in safe_facts if fact.id in candidate_ids
        ]}
        try:
            selected = self.jev_client.decide(state, jev_questions)
        except Exception:
            selected = {}
        facts_by_id = {fact.id: fact for fact in safe_facts if fact.id in candidate_ids}
        results = {}
        for question in unresolved:
            result = selected.get(question.id) if isinstance(selected, dict) else None
            identifier = getattr(result, "value", "defer")
            fact = facts_by_id.get(identifier)
            if fact is not None and any(
                option.strip().casefold() == fact.value.strip().casefold()
                for option in question.options
            ):
                option = next(
                    option for option in question.options
                    if option.strip().casefold() == fact.value.strip().casefold()
                )
                results[question.id] = FieldAnswer(question.id, option, [fact.id], "jev")
            else:
                results[question.id] = None
        return results

    def _existing_outcome(self, url):
        existing = self.history.get(url) or {}
        return self._outcome(
            existing.get("url", url),
            ApplicationStatus(existing.get("status", ApplicationStatus.deferred)),
            "already processed",
            existing.get("details", {}),
        )

    def _finish_claimed_urls(self, urls, claimed, outcomes, reason):
        for index, (url, success) in enumerate(zip(urls, claimed)):
            if success:
                canonical = self.history.get(url)["url"]
                outcomes[index] = self._finish(canonical, ApplicationStatus.deferred, reason)
        return self._refresh_unclaimed_outcomes(urls, claimed, outcomes)

    def _refresh_unclaimed_outcomes(self, urls, claimed, outcomes):
        for index, (url, success) in enumerate(zip(urls, claimed)):
            if not success:
                outcomes[index] = self._existing_outcome(url)
        return outcomes

    def _finish(self, url, status, reason, details=None):
        saved = dict(details or {})
        saved["reason"] = reason
        self.history.finish(url, status, saved)
        return self._outcome(url, status, reason, saved)

    @staticmethod
    def _outcome(url, status, reason, details=None):
        details = details or {}
        return ApplicationOutcome(
            url=url,
            status=status,
            employer=details.get("employer"),
            role=details.get("role"),
            submitted_at=details.get("submitted_at"),
            reason=reason,
        )
