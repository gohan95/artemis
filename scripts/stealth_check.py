"""Opt-in manual stealth check against public bot-detection pages. Not run by
pytest. A clean result here doesn't prove anything about a real ATS's actual
bot scoring -- the real check is `artemis apply --no-submit` against a live
posting, then inspecting the receipt.

Usage: uv run python scripts/stealth_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from artemis.browser import BrowserOptions, open_session, page_factory_for  # noqa: E402

_CHECK_URLS = (
    "https://bot.sannysoft.com/",
    "https://abrahamjuliot.github.io/creepjs/",
)

_OUTPUT_DIR = Path("browser-state/stealth_check")


async def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    options = BrowserOptions(profile_dir=Path("browser-state/stealth_check_profile"), headless=False)
    async with open_session(options) as session:
        factory = page_factory_for(session)
        for url in _CHECK_URLS:
            page = await factory(url)
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(3000)  # let async fingerprint checks finish
            name = url.split("//")[1].split("/")[0].replace(".", "_")
            screenshot_path = _OUTPUT_DIR / f"{name}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"--- {url} ---")
            print(f"  navigator.webdriver: {await page.evaluate('navigator.webdriver')!r}")
            print(f"  navigator.userAgent: {await page.evaluate('navigator.userAgent')}")
            print(f"  screenshot saved to: {screenshot_path}")
            await page.close()


if __name__ == "__main__":
    asyncio.run(main())
