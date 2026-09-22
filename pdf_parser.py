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


async def fetch_and_parse_factsheet(pdf_url: str, project_id_hint: str = "") -> Optional[FactsheetData]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            resp = await client.get(pdf_url)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch %s: %s", pdf_url, e)
            return None

    try:
        doc = fitz.open(stream=resp.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as e:
        logger.error("Failed to parse PDF %s: %s", pdf_url, e)
        return None

    if not text.strip():
        logger.warning("No extractable text in %s - may be a scanned image PDF", pdf_url)
        return None

    # The project code (e.g. "K206") and full title appear together near the
    # top, e.g. "K206 JOSEPH F. LAMB" - split the leading code off the title.
    title_match = re.search(r"\b([A-Z]\d{3,4})\b\s*(.*)", text)
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
