"""Receipt capture writes a screenshot + JSON sidecar and never raises."""

import json

import pytest

from artemis.history import ApplicationStatus
from artemis.pipeline import ApplicationOutcome
from artemis.receipts import build_receipt_hook, capture_receipt


class _FakePage:
    def __init__(self, *, raise_on_screenshot: bool = False):
        self.raise_on_screenshot = raise_on_screenshot
        self.screenshot_calls: list[dict] = []

    async def screenshot(self, *, path: str, full_page: bool) -> None:
        self.screenshot_calls.append({"path": path, "full_page": full_page})
        if self.raise_on_screenshot:
            raise RuntimeError("no display available")
        with open(path, "wb") as handle:
            handle.write(b"fake-png-bytes")


def _make_outcome(**overrides) -> ApplicationOutcome:
    defaults = dict(
        url="https://boards.greenhouse.io/acme/jobs/1",
        status=ApplicationStatus.submitted,
        reason="submission confirmed after click",
        filled_fields=("email", "full_name"),
        unresolved_fields=("cover_letter",),
    )
    defaults.update(overrides)
    return ApplicationOutcome(**defaults)


@pytest.mark.asyncio
async def test_capture_receipt_writes_screenshot_and_sidecar(tmp_path):
    page = _FakePage()
    outcome = _make_outcome()

    directory = await capture_receipt(page, outcome, receipts_root=tmp_path)

    pngs = list(directory.glob("*.png"))
    jsons = list(directory.glob("*.json"))
    assert len(pngs) == 1
    assert len(jsons) == 1

    sidecar = json.loads(jsons[0].read_text())
    assert sidecar["url"] == outcome.url
    assert sidecar["status"] == "submitted"
    assert sidecar["filled_fields"] == ["email", "full_name"]
    assert sidecar["unresolved_fields"] == ["cover_letter"]
    assert sidecar["screenshot"] == pngs[0].name


@pytest.mark.asyncio
async def test_capture_receipt_survives_a_screenshot_failure(tmp_path):
    page = _FakePage(raise_on_screenshot=True)
    outcome = _make_outcome(status=ApplicationStatus.uncertain)

    directory = await capture_receipt(page, outcome, receipts_root=tmp_path)  # must not raise

    jsons = list(directory.glob("*.json"))
    assert len(jsons) == 1
    sidecar = json.loads(jsons[0].read_text())
    assert sidecar["screenshot"] is None
    assert sidecar["status"] == "uncertain"


@pytest.mark.asyncio
async def test_same_url_reuses_the_same_receipt_directory(tmp_path):
    page = _FakePage()
    outcome_a = _make_outcome(status=ApplicationStatus.filled)
    outcome_b = _make_outcome(status=ApplicationStatus.submitted)

    dir_a = await capture_receipt(page, outcome_a, receipts_root=tmp_path)
    dir_b = await capture_receipt(page, outcome_b, receipts_root=tmp_path)

    assert dir_a == dir_b
    assert len(list(dir_a.glob("*.json"))) == 2  # both attempts recorded


@pytest.mark.asyncio
async def test_build_receipt_hook_is_callable_as_an_on_filled_hook(tmp_path):
    hook = build_receipt_hook(tmp_path)
    page = _FakePage()
    outcome = _make_outcome()

    await hook(page, outcome)  # OnFilled protocol: (page, outcome) -> None

    directory = list(tmp_path.iterdir())
    assert len(directory) == 1
