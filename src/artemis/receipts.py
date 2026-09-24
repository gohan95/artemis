"""Screenshot + JSON sidecar for every filled/submitted/uncertain outcome."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def _receipt_dir(receipts_root: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return receipts_root / digest


async def capture_receipt(page, outcome, *, receipts_root: Path) -> Path:
    directory = _receipt_dir(receipts_root, outcome.url)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")

    screenshot_path = directory / f"{timestamp}.png"
    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        # Screenshot failures shouldn't affect the recorded outcome.
        screenshot_path = None

    sidecar_path = directory / f"{timestamp}.json"
    sidecar = {
        "url": outcome.url,
        "status": outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status),
        "reason": outcome.reason,
        "captured_at": datetime.now(UTC).isoformat(),
        "filled_fields": list(outcome.filled_fields),
        "unresolved_fields": list(outcome.unresolved_fields),
        "screenshot": screenshot_path.name if screenshot_path else None,
    }
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")
    return directory


def build_receipt_hook(receipts_root: Path):
    async def on_filled(page, outcome) -> None:
        await capture_receipt(page, outcome, receipts_root=receipts_root)

    return on_filled
