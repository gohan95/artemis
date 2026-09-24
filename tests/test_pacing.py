"""Pacing determinism and the NullPacer contract."""

import random

import pytest

from artemis.pacing import HumanPacer, NullPacer, PacingProfile


class _Recorder:
    """Records every call made to it in place of asyncio.sleep, instantly."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _FakeLocator:
    """A minimal stand-in for a Playwright Locator."""

    def __init__(self, bounding_box: dict | None = None):
        self.calls: list[tuple] = []
        self._bounding_box = bounding_box
        self.page = _FakePage()

    async def fill(self, value: str) -> None:
        self.calls.append(("fill", value))

    async def press_sequentially(self, text: str, *, delay: float) -> None:
        self.calls.append(("press_sequentially", text, delay))

    async def bounding_box(self) -> dict | None:
        return self._bounding_box

    async def hover(self) -> None:
        self.calls.append(("hover",))


class _FakePage:
    def __init__(self) -> None:
        self.mouse = _FakeMouse()


class _FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float, int]] = []

    async def move(self, x: float, y: float, *, steps: int) -> None:
        self.moves.append((x, y, steps))


async def _drive(pacer) -> list:
    """One representative interaction sequence through every hook."""

    locator = _FakeLocator(bounding_box={"x": 10, "y": 20, "width": 100, "height": 30})
    await pacer.after_load()
    await pacer.think()
    await pacer.type_text(locator, "hello, world. next field")
    await pacer.before_click(locator)
    await pacer.before_submit()
    return locator.calls


@pytest.mark.asyncio
async def test_same_seed_produces_identical_delay_sequence():
    recorder_a, recorder_b = _Recorder(), _Recorder()
    pacer_a = HumanPacer(seed=1234, sleep=recorder_a)
    pacer_b = HumanPacer(seed=1234, sleep=recorder_b)

    await _drive(pacer_a)
    await _drive(pacer_b)

    assert recorder_a.calls == recorder_b.calls
    assert recorder_a.calls  # sanity: something was actually recorded


@pytest.mark.asyncio
async def test_different_seeds_diverge():
    recorder_a, recorder_b = _Recorder(), _Recorder()
    pacer_a = HumanPacer(seed=1234, sleep=recorder_a)
    pacer_b = HumanPacer(seed=9999, sleep=recorder_b)

    await _drive(pacer_a)
    await _drive(pacer_b)

    assert recorder_a.calls != recorder_b.calls


@pytest.mark.asyncio
async def test_pacer_never_touches_global_random():
    random.seed(0)
    expected = random.random()

    random.seed(0)
    await _drive(HumanPacer(seed=42, sleep=_Recorder()))
    actual = random.random()

    assert actual == expected


@pytest.mark.asyncio
async def test_null_pacer_type_text_still_fills():
    pacer = NullPacer()
    locator = _FakeLocator()

    await pacer.type_text(locator, "some value")

    assert locator.calls == [("fill", "some value")]


@pytest.mark.asyncio
async def test_null_pacer_hooks_are_no_ops_and_never_sleep():
    pacer = NullPacer()
    locator = _FakeLocator(bounding_box={"x": 0, "y": 0, "width": 10, "height": 10})

    await pacer.think()
    await pacer.after_load()
    await pacer.before_submit()
    await pacer.before_click(locator)

    assert locator.calls == []
    assert locator.page.mouse.moves == []


@pytest.mark.asyncio
async def test_key_delays_stay_within_configured_profile():
    profile = PacingProfile(key_delay_ms=(50.0, 60.0))
    pacer = HumanPacer(profile, seed=7, sleep=_Recorder())
    locator = _FakeLocator()

    await pacer.type_text(locator, "abcdefghij" * 5)

    delays = [call[2] for call in locator.calls if call[0] == "press_sequentially"]
    assert delays
    assert all(50.0 <= d <= 60.0 for d in delays)


@pytest.mark.asyncio
async def test_think_delay_converts_ms_to_seconds():
    profile = PacingProfile(think_ms=(1000.0, 1000.0))
    recorder = _Recorder()
    pacer = HumanPacer(profile, seed=1, sleep=recorder)

    await pacer.think()

    assert recorder.calls == [1.0]


@pytest.mark.asyncio
async def test_before_click_tolerates_missing_bounding_box():
    pacer = HumanPacer(seed=1, sleep=_Recorder())
    locator = _FakeLocator(bounding_box=None)

    await pacer.before_click(locator)  # must not raise

    assert locator.calls == []
    assert locator.page.mouse.moves == []


@pytest.mark.asyncio
async def test_before_click_swallows_locator_errors():
    class _RaisingLocator(_FakeLocator):
        async def bounding_box(self):
            raise RuntimeError("detached from DOM")

    pacer = HumanPacer(seed=1, sleep=_Recorder())
    await pacer.before_click(_RaisingLocator())  # must not raise


@pytest.mark.asyncio
async def test_before_click_moves_mouse_then_hovers():
    pacer = HumanPacer(seed=1, sleep=_Recorder())
    locator = _FakeLocator(bounding_box={"x": 0, "y": 0, "width": 100, "height": 40})

    await pacer.before_click(locator)

    assert len(locator.page.mouse.moves) == 2  # overshoot + correction
    assert locator.calls == [("hover",)]
