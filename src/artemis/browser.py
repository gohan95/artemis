"""Browser session ownership via patchright (a drop-in, undetected Playwright fork).

Don't add user-agent/header overrides: a spoofed value disagrees with the
real Client Hints/TLS/WebGL signals and makes the browser *more* detectable,
not less. Patchright's own docs say the same. This module only sets
locale/timezone/color_scheme, derived deterministically from the profile
directory so a persistent context's identity stays consistent across runs.
"""

from __future__ import annotations

import hashlib
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Protocol


@dataclass(frozen=True)
class Fingerprint:
    locale: str
    timezone_id: str
    color_scheme: Literal["light", "dark", "no-preference"]

    @classmethod
    def for_profile(cls, profile_dir: Path) -> "Fingerprint":
        seed = int(
            hashlib.sha256(str(profile_dir.resolve()).encode("utf-8")).hexdigest(),
            16,
        )
        rng = random.Random(seed)
        return cls(
            locale=rng.choice(("en-US", "en-GB")),
            timezone_id=rng.choice(
                ("America/New_York", "America/Los_Angeles", "America/Chicago")
            ),
            color_scheme=rng.choice(("light", "dark")),
        )

    def accept_language(self) -> str:
        return f"{self.locale},{self.locale.split('-')[0]};q=0.9"


@dataclass(frozen=True)
class ProxyConfig:
    server: str
    username: str | None = None
    password: str | None = None

    def to_playwright(self) -> dict[str, str]:
        config = {"server": self.server}
        if self.username:
            config["username"] = self.username
            config["password"] = self.password or ""
        return config


@dataclass(frozen=True)
class BrowserOptions:
    profile_dir: Path
    headless: bool = False
    channel: str | None = "chrome"
    proxy: ProxyConfig | None = None
    slow_mo_ms: float = 0.0


class BrowserSession(Protocol):
    async def new_page(self, url: str) -> Any: ...

    async def close(self) -> None: ...


class _ContextSession:
    """`launch_persistent_context` returns a context, not a Browser, and
    already opens with one blank page -- reuse it instead of opening a
    fresh one, since closing the last page closes the whole context."""

    def __init__(self, context: Any) -> None:
        self._context = context
        self._spare_page = context.pages[0] if context.pages else None

    async def new_page(self, url: str) -> Any:
        if self._spare_page is not None:
            page = self._spare_page
            self._spare_page = None
            return page
        return await self._context.new_page()

    async def close(self) -> None:
        await self._context.close()


@asynccontextmanager
async def open_session(options: BrowserOptions) -> AsyncIterator[BrowserSession]:
    from patchright.async_api import async_playwright

    fingerprint = Fingerprint.for_profile(options.profile_dir)
    options.profile_dir.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        launch_kwargs: dict[str, Any] = dict(
            user_data_dir=str(options.profile_dir),
            headless=options.headless,
            no_viewport=True,
            locale=fingerprint.locale,
            timezone_id=fingerprint.timezone_id,
            color_scheme=fingerprint.color_scheme,
            extra_http_headers={"Accept-Language": fingerprint.accept_language()},
        )
        if options.channel:
            launch_kwargs["channel"] = options.channel
        if options.proxy is not None:
            launch_kwargs["proxy"] = options.proxy.to_playwright()
        if options.slow_mo_ms:
            launch_kwargs["slow_mo"] = options.slow_mo_ms

        try:
            context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as error:
            if options.channel:
                # Real Chrome may not be installed; fall back to bundled Chromium.
                launch_kwargs.pop("channel", None)
                try:
                    context = await playwright.chromium.launch_persistent_context(
                        **launch_kwargs
                    )
                except Exception:
                    raise RuntimeError(
                        f"browser profile {options.profile_dir} could not be opened"
                    ) from error
            else:
                raise RuntimeError(
                    f"browser profile {options.profile_dir} could not be opened"
                ) from error

        session = _ContextSession(context)
        try:
            yield session
        finally:
            await session.close()


def page_factory_for(session: BrowserSession) -> Callable[[str], Awaitable[Any]]:
    async def factory(url: str) -> Any:
        return await session.new_page(url)

    return factory
