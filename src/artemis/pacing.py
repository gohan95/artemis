"""Human pacing for form interaction: typing rhythm, think-time, mouse paths.

`NullPacer` is the default for every adapter, so tests stay instant and
deterministic. `HumanPacer` draws delays from its own seeded RNG (never the
global `random` module) and takes an injectable `sleep`, so pacing itself is
unit-testable without real time passing.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


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


@dataclass(frozen=True)
class PacingProfile:
    """Delay ranges; *_ms fields are milliseconds."""

    key_delay_ms: tuple[float, float] = (55.0, 165.0)
    think_ms: tuple[float, float] = (400.0, 1800.0)
    after_load_ms: tuple[float, float] = (900.0, 2600.0)
    before_submit_ms: tuple[float, float] = (1500.0, 4000.0)
    mouse_steps: tuple[int, int] = (8, 22)
    word_boundary_pause_chance: float = 0.15
    word_boundary_pause_ms: tuple[float, float] = (250.0, 900.0)
    # Deliberately unused by default -- see type_text.
    typo_rate: float = 0.0


class HumanPacer:
    def __init__(
        self,
        profile: PacingProfile | None = None,
        *,
        seed: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._profile = profile or PacingProfile()
        self._rng = random.Random(seed)
        self._sleep = sleep

    def _uniform_seconds(self, bounds_ms: tuple[float, float]) -> float:
        return self._rng.uniform(*bounds_ms) / 1000.0

    async def think(self) -> None:
        await self._sleep(self._uniform_seconds(self._profile.think_ms))

    async def after_load(self) -> None:
        await self._sleep(self._uniform_seconds(self._profile.after_load_ms))

    async def before_submit(self) -> None:
        await self._sleep(self._uniform_seconds(self._profile.before_submit_ms))

    async def type_text(self, locator, text: str) -> None:
        # Not a typo-and-correct simulation: a stray keystroke can corrupt a
        # masked/controlled field and nothing reads it back to check, so
        # typo_rate stays at 0 -- keystroke timing, not content, is the
        # signal worth varying.
        await locator.fill("")
        word_boundary_chars = frozenset(" .,;:!?\n")
        for char in text:
            delay_ms = self._rng.uniform(*self._profile.key_delay_ms)
            await locator.press_sequentially(char, delay=delay_ms)
            if (
                char in word_boundary_chars
                and self._rng.random() < self._profile.word_boundary_pause_chance
            ):
                await self._sleep(self._uniform_seconds(self._profile.word_boundary_pause_ms))

    async def before_click(self, locator) -> None:
        try:
            box = await locator.bounding_box()
            if not box:
                return
            target_x = box["x"] + box["width"] * self._rng.uniform(0.3, 0.7)
            target_y = box["y"] + box["height"] * self._rng.uniform(0.3, 0.7)
            page = locator.page
            steps = self._rng.randint(*self._profile.mouse_steps)
            await page.mouse.move(
                target_x + self._rng.uniform(-24.0, 24.0),
                target_y + self._rng.uniform(-24.0, 24.0),
                steps=max(1, steps // 2),
            )
            await page.mouse.move(target_x, target_y, steps=steps)
            await locator.hover()
        except Exception:
            return
