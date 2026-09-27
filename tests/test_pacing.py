"""The NullPacer contract."""

import pytest

from artemis.pacing import NullPacer


class _FakeLocator:
    """A minimal stand-in for a Playwright Locator."""

    def __init__(self, bounding_box: dict | None = None):
        self.calls: list[tuple] = []
        self._bounding_box = bounding_box
        self.page = _FakePage()

    async def fill(self, value: str) -> None:
        self.calls.append(("fill", value))


class _FakePage:
    def __init__(self) -> None:
        self.mouse = _FakeMouse()


class _FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float, int]] = []


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
