"""
Discovers real factsheet PDF links on nycsca.org's Projects page.

This page is a JavaScript-rendered app - a plain HTTP fetch only returns
template placeholders like {{proj.FactSheetUrl}}, confirmed by directly
inspecting its raw HTML. Playwright renders it as a real browser would,
so the actual hrefs appear in the DOM.

The page appears to expose different project categories via hash-anchored
sections (e.g. #New-Schools-31, #Projects-in-Construction-37). Since this
is a client-side SPA, the hash is read by the app's own router after load,
not sent to the server - so each category is visited as a separate
navigation with that hash appended, and the resulting rendered DOM is
scraped after each one.

IMPORTANT: the exact category hashes below are the ones seen in this
project's chat history. If NYCSCA adds or renames a category, add its
hash here - there is no way to discover the full list without rendering
the page's top-level navigation first, which this does NOT attempt
automatically (kept deliberately simple and explicit rather than guessing
at additional selectors that haven't been confirmed against the live page).
"""

import logging
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .renderer import get_renderer

logger = logging.getLogger(__name__)

BASE_URL = "https://nycsca.org/quick-links-home/projects"

# Known category hashes - extend this list if NYCSCA adds more categories.
CATEGORY_HASHES = [
    "New-Schools-31",
    "Projects-in-Construction-37",
]


@dataclass
class DiscoveredFactsheet:
    project_name_hint: str
    pdf_url: str
    category: str


def _extract_factsheet_links(html: str, category: str) -> List[DiscoveredFactsheet]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[DiscoveredFactsheet] = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        full_url = urljoin(BASE_URL, href)
        # Only SCA project factsheets. The page also links to safety manuals,
        # general conditions, design events and public-art PDFs.
        path = full_url.split("?", 1)[0].lower()
        filename = path.rsplit("/", 1)[-1]
        if "/app_resources/projects/" not in path or "artwork" in filename or "public art" in filename:
            continue
        if not re.search(r"(?:^|[^a-z0-9])[kmqxr]\s*\d{2,4}(?:[^a-z0-9]|$)", filename, re.I):
            continue
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # Best-effort project name: nearest heading/text before the link,
        # falling back to the link's own text, then the filename.
        name_hint = a.get_text(strip=True)
        if not name_hint:
            parent = a.find_parent()
            if parent:
                heading = parent.find(["h1", "h2", "h3", "h4"])
                if heading:
                    name_hint = heading.get_text(strip=True)
        if not name_hint:
            name_hint = full_url.rsplit("/", 1)[-1]

        results.append(DiscoveredFactsheet(project_name_hint=name_hint, pdf_url=full_url, category=category))

    return results


async def discover_factsheet_pdfs() -> List[DiscoveredFactsheet]:
    renderer = get_renderer()
    all_results: List[DiscoveredFactsheet] = []

    for cat_hash in CATEGORY_HASHES:
        url = f"{BASE_URL}#{cat_hash}"
        logger.info("Rendering %s", url)
        try:
            html = await renderer.render(url, wait_seconds=4.0)
        except Exception as e:
            logger.error("Failed to render %s: %s", url, e)
            continue

        if "{{proj." in html or "{{sec}}" in html:
            logger.warning(
                "Category %s still shows unrendered template markup after waiting - "
                "the page may need a longer wait, or a real click on the tab rather "
                "than relying on the hash alone. Skipping this category for now.",
                cat_hash,
            )
            continue

        found = _extract_factsheet_links(html, category=cat_hash)
        logger.info("Category %s: found %d factsheet PDF links", cat_hash, len(found))
        all_results.extend(found)

    return all_results
