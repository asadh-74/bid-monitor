"""Public procurement result pages; parse only fields actually present in source markup."""
import logging
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .renderer import get_renderer

log = logging.getLogger(__name__)
DASNY = "https://www.dasny.org/opportunities/rfps-bids/construction-contracts"
NYSCR = "https://www.nyscr.ny.gov/Ads/Search"
AWARDS = "https://nycsca.org/Doing-Business/Contracting-with-Us/Capital-Improvements-Anticipated-Contract-Awards"


def _label(text, name):
    match = re.search(rf"{re.escape(name)}\s*:?\s*(.+?)(?=\n|$)", text, re.I)
    return match.group(1).strip() if match else ""


def parse_dasny(html):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    seen = set()
    for link in soup.find_all("a", href=True):
        if "View the full details for this opportunity" not in link.get_text(" ", strip=True):
            continue
        url = urljoin(DASNY, link["href"])
        if url in seen:
            continue
        seen.add(url)
        # Each result has its own heading and detail link. Avoid neighboring records.
        block = link
        for parent in link.parents:
            if parent.name in ("article", "li") or (parent.name == "div" and parent.find(re.compile("^h[2-5]$")) and "Solicitation" in parent.get_text(" ", strip=True)):
                block = parent
                break
        text = block.get_text("\n", strip=True)
        heading = block.find(re.compile("^h[2-5]$"))
        title = heading.get_text(" ", strip=True) if heading else ""
        if not title:
            title = text.split("\n", 1)[0]
        solicitation = _label(text, "Solicitation #")
        if not solicitation or not title:
            log.warning("Skipping DASNY record with missing title/solicitation: %s", url)
            continue
        records.append(dict(source="dasny", source_id=solicitation, source_url=url, title=title,
                            stage="advertised", deadline=_label(text, "Due Date"), description=text[:3000]))
    return records


def parse_nyscr(html, agency_filter="", construction_only=True):
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select(".opp-list-item[data-ad-id]"):
        fields = {}
        for row in card.select(".d-flex"):
            children = row.find_all("div", recursive=False)
            if len(children) >= 2:
                label = children[0].get_text(" ", strip=True).rstrip(":").lower()
                if label in ("agency", "company", "category", "due date", "cr#", "location"):
                    fields[label] = children[1].get_text(" ", strip=True)
        title_node = card.select_one('[title^="Full Title:"]')
        title = title_node.get_text(" ", strip=True) if title_node else ""
        agency = fields.get("agency", "")
        if agency_filter and agency_filter.lower() not in agency.lower():
            continue
        if construction_only and "construction" not in fields.get("category", "").lower():
            continue
        cr = card.get("data-ad-id", "")
        if not cr or not title:
            continue
        records.append(dict(source="nyscr", source_id=cr, source_url=NYSCR,
                            title=title, stage="advertised", deadline=fields.get("due date", ""),
                            description=f"Agency: {agency}; Category: {fields.get('category', '')}; Location: {fields.get('location', '')}"))
    return records


async def collect_dasny():
    html = await get_renderer().render(DASNY, wait_seconds=3)
    rows = parse_dasny(html)
    if not rows:
        raise ValueError("DASNY returned no valid opportunity records; check page structure")
    return rows


async def collect_nyscr():
    # The public initial page has a paginated result set. Configure a specific
    # agency to prevent unrelated statewide notices being imported as SCA work.
    import os
    agency = os.getenv("NYSCR_AGENCY_FILTER", "")
    if not agency:
        raise ValueError("Set NYSCR_AGENCY_FILTER to an agency name before enabling this collector")
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        response = await client.get(NYSCR)
        response.raise_for_status()
    return parse_nyscr(response.text, agency_filter=agency)


async def collect_awards():
    html = await get_renderer().render(AWARDS, wait_seconds=4)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.find_all("table"):
        headings = [x.get_text(" ", strip=True).lower() for x in table.select("tr:first-child th")]
        if not headings or not any("contract" in h or "project" in h for h in headings):
            continue
        for tr in table.select("tr"):
            cells = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            if len(cells) != len(headings):
                continue
            fields = dict(zip(headings, cells))
            title = next((v for k, v in fields.items() if "project" in k or "description" in k), "")
            key = next((v for k, v in fields.items() if "contract" in k and ("number" in k or "no" in k)), "")
            if title and key:
                rows.append(dict(source="sca_awards", source_id=key, source_url=AWARDS, title=title,
                                 stage="anticipated_award", description="; ".join(f"{k}: {v}" for k, v in fields.items())))
    if not rows:
        raise ValueError("SCA awards page has no verified contract table; selector needs inspection")
    return rows
