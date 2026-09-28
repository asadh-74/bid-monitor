"""Public procurement result pages; parse only fields actually present in source markup."""
import logging
import re
import asyncio
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .renderer import get_renderer

log = logging.getLogger(__name__)
DASNY = "https://www.dasny.org/opportunities/rfps-bids/construction-contracts"
NYSCR = "https://www.nyscr.ny.gov/Ads/Search"
AWARDS = "https://www.nycsca.org/Doing-Business/Contracting-with-Us/Capital-Improvements-Anticipated-Contract-Awards"
SCA_CIP_DATA = "https://data.cityofnewyork.us/resource/tsak-vtv3.json"
SCA_CAP_DATA = "https://data.cityofnewyork.us/resource/6m3u-8rbh.json"


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
        block = link.find_parent("div", class_="rfp-bid-wrapper")
        if not block:
            continue
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
    renderer = get_renderer()
    rows = {}
    for page in range(20):
        url = DASNY if page == 0 else f"{DASNY}?page={page}"
        html = await renderer.render(url, wait_seconds=3)
        parsed = parse_dasny(html)
        for row in parsed:
            rows[row["source_id"]] = row
        if not parsed or not BeautifulSoup(html, "html.parser").select_one('a[rel="next"], a[title="Go to next page"]'):
            break
    if not rows:
        raise ValueError("DASNY returned no valid opportunity records; check page structure")
    return list(rows.values())


async def collect_nyscr():
    import os
    agency = os.getenv("NYSCR_AGENCY_FILTER", "")
    max_pages = min(max(int(os.getenv("NYSCR_MAX_PAGES", "40")), 1), 100)
    collected = {}
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        for page in range(max_pages):
            response = await client.get(NYSCR, params={"Skip": page * 25, "Status": "Open"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".opp-list-item[data-ad-id]")
            if not cards:
                break
            for row in parse_nyscr(response.text, agency_filter=agency):
                collected[row["source_id"]] = row
            if len(cards) < 25:
                break
            await asyncio.sleep(0.5)
        else:
            log.warning("NYSCR reached NYSCR_MAX_PAGES=%d; coverage may be incomplete", max_pages)
    return list(collected.values())


async def collect_awards():
    """Official NYC Open Data exports of upcoming SCA CIP and capacity contracts."""
    rows = []
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        for kind, url in (("sca_cip_upcoming", SCA_CIP_DATA), ("sca_capacity_upcoming", SCA_CAP_DATA)):
            response = await client.get(url, params={"$limit": 5000})
            response.raise_for_status()
            for item in response.json():
                title = item.get("upcoming_project_name", "")
                scope = item.get("upcoming_project_description", "")
                design = item.get("upcoming_project_design_number", "")
                if not title or not scope:
                    continue
                identity = f"{design or title}|{scope}|{item.get('upcoming_project_borough_', '')}"
                rows.append(dict(source=kind, source_id=identity, source_url=url,
                                 title=f"{title} — {scope}", stage="anticipated",
                                 deadline=item.get("upcoming_project_advertised_date", "") or
                                          item.get("upcoming_project_projected_advertisement_date", ""),
                                 description=f"Category: {item.get('upcoming_project_category','')}; "
                                             f"Status: {item.get('upcoming_project_status_','')}; "
                                             f"Borough: {item.get('upcoming_project_borough_','')}; "
                                             f"Budget: {item.get('upcoming_project_budget_range','')}"))
    if not rows:
        raise ValueError("NYC Open Data returned no upcoming SCA contracts")
    return rows
