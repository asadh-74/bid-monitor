"""
Downloads a factsheet PDF and extracts its structured fields.

Field layout confirmed directly from real factsheet screenshots (K206, K004,
K280, K358): each PDF has a title (e.g. "K206 JOSEPH F. LAMB"), then labeled
fields in a consistent "Label:\nValue" pattern - Project Type, Location,
School District, Capacity, Grades Served, Contract Award, Construction
Start, Occupancy Date, Architect/Engineer, General Contractor.
"""

import logging
import re
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import unquote, urlsplit

import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)


@dataclass
class FactsheetData:
    project_id: str          # e.g. "K206" - the building/project code
    project_name: str        # e.g. "K206 JOSEPH F. LAMB"
    project_type: str
    location: str
    school_district: str
    capacity: str
    grades_served: str
    contract_award: str
    construction_start: str
    occupancy_date: str
    architect_engineer: str
    general_contractor: str
    source_pdf_url: str


def _field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


LEGACY_LABELS = ("PROJECT TYPE", "LOCATION", "SCHOOL DISTRICT", "CAPACITY",
                 "CONTRACT AWARD", "CONSTRUCTION START", "ANTICIPATED OCCUPANCY",
                 "ARCHITECT/ENGINEER", "CONSTRUCTION MANAGER", "GENERAL CONTRACTOR")


def _legacy_factsheet(doc, text: str, pdf_url: str) -> Optional[FactsheetData]:
    """Read the older two-column SCA factsheets from their right-hand field column."""
    # PyMuPDF 1.24 groups some right-column text with the left column in
    # get_text("blocks"). A geometric clip extracts the labels consistently.
    right = "\n".join(page.get_text("text", clip=fitz.Rect(
        page.rect.width * 0.65, 0, page.rect.width, page.rect.height * 0.88))
        for page in doc)
    labels = [re.escape(label).replace(r"\ ", r"\s+") for label in LEGACY_LABELS]
    markers = list(re.finditer(r"\b(?:" + "|".join(labels) + r")\b", right))
    fields = {}
    for index, marker in enumerate(markers):
        value = right[marker.end():markers[index + 1].start() if index + 1 < len(markers) else None]
        # The footer often follows the final contractor on the same text block.
        value = re.split(r"\n\s*\n|\bNEW YORK CITY SCHOOL\s+CONSTRUCTION AUTHORITY\b", value.strip(), maxsplit=1)[0]
        fields[" ".join(marker.group().split())] = " ".join(value.split()).strip(" :")
    filename = unquote(urlsplit(pdf_url).path.rsplit("/", 1)[-1])
    code = re.search(r"\b([KMQXR]\d{2,4})\b", filename, re.I)
    if not (code and fields.get("PROJECT TYPE") and fields.get("LOCATION")
            and fields.get("ARCHITECT/ENGINEER") and fields.get("GENERAL CONTRACTOR")):
        return None
    return FactsheetData(
        project_id=code.group(1).upper(), project_name=text.strip().splitlines()[0].strip(),
        project_type=fields["PROJECT TYPE"], location=fields["LOCATION"],
        school_district=fields.get("SCHOOL DISTRICT", ""), capacity=fields.get("CAPACITY", ""),
        grades_served="", contract_award=fields.get("CONTRACT AWARD", ""),
        construction_start=fields.get("CONSTRUCTION START", ""),
        occupancy_date=fields.get("ANTICIPATED OCCUPANCY", ""),
        architect_engineer=fields["ARCHITECT/ENGINEER"],
        general_contractor=fields["GENERAL CONTRACTOR"], source_pdf_url=pdf_url)


FACTSHEET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


async def fetch_and_parse_factsheet(pdf_url: str, project_id_hint: str = "") -> Optional[FactsheetData]:
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers=FACTSHEET_HEADERS
    ) as client:
        try:
            resp = await client.get(pdf_url)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch %s: %s", pdf_url, e)
            return None

    try:
        doc = fitz.open(stream=resp.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        legacy = None
        if not all(re.search(rf"\b{label}\b\s*:", text, re.I)
                   for label in ("Project Type", "Location", "General Contractor")):
            legacy = _legacy_factsheet(doc, text, pdf_url)
        doc.close()
    except Exception as e:
        logger.error("Failed to parse PDF %s: %s", pdf_url, e)
        return None

    if not text.strip():
        logger.warning("No extractable text in %s - may be a scanned image PDF", pdf_url)
        return None

    if legacy:
        return legacy

    # A project code alone is insufficient: unrelated PDFs on the page contain
    # incidental school codes and previously polluted the live factsheet table.
    if not all(re.search(rf"\b{label}\b\s*:", text, re.I)
               for label in ("Project Type", "Location", "General Contractor")):
        logger.warning("Skipping non-project PDF %s", pdf_url)
        return None

    # The project code (e.g. "K206") and full title appear together near the
    # top, e.g. "K206 JOSEPH F. LAMB" - split the leading code off the title.
    title_match = re.search(r"\b([KMQXR]\d{3,4})\b\s*([^\n]*)", text)
    if not title_match:
        logger.warning("Skipping factsheet without a school project code: %s", pdf_url)
        return None
    project_id = title_match.group(1) if title_match else project_id_hint
    project_name = (title_match.group(0).strip() if title_match else text.strip().split("\n", 1)[0])

    return FactsheetData(
        project_id=project_id or project_id_hint,
        project_name=project_name,
        project_type=_field(text, "Project Type"),
        location=_field(text, "Location"),
        school_district=_field(text, "School District"),
        capacity=_field(text, "Capacity"),
        grades_served=_field(text, "Grades Served"),
        contract_award=_field(text, "Contract Award"),
        construction_start=_field(text, "Construction Start"),
        occupancy_date=_field(text, "Occupancy Date"),
        architect_engineer=_field(text, "Architect/Engineer") or _field(text, "Architect"),
        general_contractor=_field(text, "General Contractor"),
        source_pdf_url=pdf_url,
    )
