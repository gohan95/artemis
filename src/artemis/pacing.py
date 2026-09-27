"""Timing hooks the ATS adapters call around fills/clicks.

`NullPacer` is the only implementation -- every hook is a no-op except
`type_text`, which still fills the control. Kept as a protocol (rather than
calling `locator.fill()` directly in `ats/base.py`) so a real pacing
implementation can be dropped in later without touching the adapters, if it
turns out to actually be needed.
"""

from __future__ import annotations

from typing import Protocol


class Pacer(Protocol):
    """Timing/motion hooks the ATS adapters call. Must never raise."""

    async def think(self) -> None: ...

    async def type_text(self, locator, text: str) -> None: ...

    async def before_click(self, locator) -> None: ...

    async def after_load(self) -> None: ...

    async def before_submit(self) -> None: ...


class NullPacer:
    """No-op except `type_text`, which still fills -- must not silently
    leave a field empty."""

    async def think(self) -> None:
        return None

    async def type_text(self, locator, text: str) -> None:
        await locator.fill(text)

    async def before_click(self, locator) -> None:
        return None

    async def after_load(self) -> None:
        return None

    async def before_submit(self) -> None:
        return None
