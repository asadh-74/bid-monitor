"""
Page rendering abstraction.

PlaywrightRenderer is the real, working implementation - free, self-hosted,
no per-page cost. FirecrawlRenderer is an optional alternative behind the
same interface, only used if you explicitly set RENDERER=firecrawl and
provide FIRECRAWL_API_KEY - matching the "free-first, paid services as
optional adapters" principle. It is not needed for this to work.
"""

import abc
import asyncio
import os
from typing import Optional

from playwright.async_api import async_playwright


class Renderer(abc.ABC):
    @abc.abstractmethod
    async def render(self, url: str, wait_seconds: float = 3.0) -> str:
        """Return the fully-rendered HTML of a page after JavaScript runs."""
        raise NotImplementedError


class PlaywrightRenderer(Renderer):
    """Launches a real headless Chromium via Playwright. This is the
    genuine equivalent of what a person would see opening the page in a
    browser - no template placeholders, no empty shell."""

    async def render(self, url: str, wait_seconds: float = 3.0) -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                # Some SPAs never fully go idle (polling, websockets) -
                # fall back to a plain load + fixed wait instead of failing.
                await page.goto(url, wait_until="load", timeout=45000)
            await asyncio.sleep(wait_seconds)
            html = await page.content()
            await browser.close()
            return html


class FirecrawlRenderer(Renderer):
    """Optional paid alternative. Only instantiated if RENDERER=firecrawl
    is explicitly set - see get_renderer() below."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def render(self, url: str, wait_seconds: float = 3.0) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"url": url, "formats": ["html"]},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("html", "")


def get_renderer() -> Renderer:
    choice = os.getenv("RENDERER", "playwright").lower()
    if choice == "firecrawl":
        key = os.getenv("FIRECRAWL_API_KEY")
        if not key:
            raise RuntimeError("RENDERER=firecrawl but FIRECRAWL_API_KEY is not set")
        return FirecrawlRenderer(key)
    return PlaywrightRenderer()
