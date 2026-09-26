"""
Parses scainfohub.azurewebsites.net's Advertised Bids / Limited Bids tables.

Confirmed real structure from an actual screenshot: a plain HTML <table>
with columns School Description/Range, Solicit#, Doc Avail, Docs Locator,
Pre Bid Meeting Date, Bid Open, Specialist, Contract Type, Phone/Email.
This is a real table once rendered, so it's parsed directly by column
position rather than by regex-guessing at text patterns.
"""

import logging
import re
from dataclasses import dataclass
from typing import List

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class ScaBidRow:
    school_description: str
    solicitation_number: str
    doc_avail_date: str
    pre_bid_meeting_date: str
    bid_open_date: str
    specialist_name: str
    contract_type: str
    specialist_phone: str
    specialist_email: str
    source: str  # "advertised" or "limited"


def parse_scainfohub_table(html: str, source: str) -> List[ScaBidRow]:
    soup = BeautifulSoup(html, "html.parser")
    all_tables = soup.find_all("table")
    if not all_tables:
        logger.warning("No <table> found on scainfohub %s page - it may not have rendered fully.", source)
        return []

    table = max(all_tables, key=lambda t: len(t.find_all("td")))

    rows_out: List[ScaBidRow] = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue  # header row or malformed row

        school_desc, solicit, doc_avail, _docs_locator, pre_bid, bid_open, specialist, contract_type = cells[:8]
        phone_email_cell = cells[8] if len(cells) > 8 else ""

        phone_match = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", phone_email_cell)
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", phone_email_cell)

        rows_out.append(ScaBidRow(
            school_description=school_desc,
            solicitation_number=solicit,
            doc_avail_date=doc_avail,
            pre_bid_meeting_date=pre_bid,
            bid_open_date=bid_open,
            specialist_name=specialist,
            contract_type=contract_type,
            specialist_phone=phone_match.group(0) if phone_match else "",
            specialist_email=email_match.group(0) if email_match else "",
            source=source,
        ))

    return rows_out
