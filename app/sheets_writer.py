"""
Writes factsheet data to a dedicated "SCA Factsheets" tab.

This is deliberately a separate tab from your main "Bids" tab, not merged
into it - a factsheet describes an already-awarded/in-progress project
(it has a General Contractor, a construction start date already in the
past for many entries), which is a different kind of record than an open
bid opportunity. Cross-reference the two by project code (e.g. "K206")
if you want to enrich a Bids row with its contractor once awarded.
"""

import json
import logging
import os
from typing import List

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .pdf_parser import FactsheetData
from .scainfohub_parser import ScaBidRow

logger = logging.getLogger(__name__)

SHEET_TAB = "SCA Factsheets"
HEADERS = [
    "Project ID", "Project Name", "Project Type", "Location", "School District",
    "Capacity", "Grades Served", "Contract Award", "Construction Start",
    "Occupancy Date", "Architect/Engineer", "General Contractor", "Source PDF URL",
]

BIDS_TAB = "SCA Advertised-Limited Bids"
BIDS_HEADERS = [
    "School Description", "Solicitation #", "Doc Avail Date", "Pre-Bid Meeting Date",
    "Bid Open Date", "Specialist Name", "Contract Type", "Specialist Phone",
    "Specialist Email", "Source",
]


def _get_sheets_service():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def _ensure_header(service, sheet_id: str):
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{SHEET_TAB}!A1:M1")
        .execute()
    )
    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()


def write_factsheets(rows: List[FactsheetData]):
    if not rows:
        logger.info("No factsheet rows to write.")
        return

    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    service = _get_sheets_service()
    _ensure_header(service, sheet_id)

    # Read existing Project IDs so we upsert (update if present, append if
    # new) instead of duplicating a row every run.
    existing = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{SHEET_TAB}!A:A")
        .execute()
        .get("values", [])
    )
    existing_ids = {row[0]: idx + 1 for idx, row in enumerate(existing) if row}  # 1-based row numbers

    to_append = []
    updates = []

    for r in rows:
        values = [
            r.project_id, r.project_name, r.project_type, r.location, r.school_district,
            r.capacity, r.grades_served, r.contract_award, r.construction_start,
            r.occupancy_date, r.architect_engineer, r.general_contractor, r.source_pdf_url,
        ]
        if r.project_id in existing_ids:
            row_num = existing_ids[r.project_id]
            updates.append({"range": f"{SHEET_TAB}!A{row_num}:M{row_num}", "values": [values]})
        else:
            to_append.append(values)

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        logger.info("Updated %d existing factsheet rows.", len(updates))

    if to_append:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{SHEET_TAB}!A:M",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": to_append},
        ).execute()
        logger.info("Appended %d new factsheet rows.", len(to_append))


def write_sca_bids(rows: List[ScaBidRow]):
    if not rows:
        logger.info("No scainfohub bid rows to write.")
        return

    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    service = _get_sheets_service()

    header_check = (
        service.spreadsheets().values()
        .get(spreadsheetId=sheet_id, range=f"{BIDS_TAB}!A1:J1")
        .execute()
    )
    if not header_check.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"{BIDS_TAB}!A1",
            valueInputOption="RAW", body={"values": [BIDS_HEADERS]},
        ).execute()

    existing = (
        service.spreadsheets().values()
        .get(spreadsheetId=sheet_id, range=f"{BIDS_TAB}!B:B")  # column B = Solicitation #
        .execute()
        .get("values", [])
    )
    existing_ids = {row[0]: idx + 1 for idx, row in enumerate(existing) if row}

    to_append, updates = [], []
    for r in rows:
        values = [
            r.school_description, r.solicitation_number, r.doc_avail_date, r.pre_bid_meeting_date,
            r.bid_open_date, r.specialist_name, r.contract_type, r.specialist_phone,
            r.specialist_email, r.source,
        ]
        if r.solicitation_number in existing_ids:
            row_num = existing_ids[r.solicitation_number]
            updates.append({"range": f"{BIDS_TAB}!A{row_num}:J{row_num}", "values": [values]})
        else:
            to_append.append(values)

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id, body={"valueInputOption": "RAW", "data": updates}
        ).execute()
        logger.info("Updated %d existing scainfohub bid rows.", len(updates))

    if to_append:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{BIDS_TAB}!A:J",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": to_append},
        ).execute()
        logger.info("Appended %d new scainfohub bid rows.", len(to_append))


def read_all_live_data() -> dict:
    """Reads both tabs back out for the live dashboard/API - this is what
    makes the data 'live on the website' rather than only in the sheet."""
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    service = _get_sheets_service()

    def _read(tab: str, headers: List[str]) -> List[dict]:
        result = (
            service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"{tab}!A2:Z1000")
            .execute()
            .get("values", [])
        )
        return [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in result]

    return {
        "factsheets": _read(SHEET_TAB, HEADERS),
        "advertised_limited_bids": _read(BIDS_TAB, BIDS_HEADERS),
    }
