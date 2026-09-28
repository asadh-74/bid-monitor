"""One durable Google Sheets project ledger for the free Render deployment."""
import logging
import os
import re
from datetime import datetime, timezone

from .sheets_writer import _get_sheets_service

log = logging.getLogger(__name__)
TAB = "Projects"
HEADERS = ["Source", "Source ID", "Source URL", "Title", "Stage", "Deadline",
           "Contractor", "Contractor Email", "Contact Evidence", "Description",
           "First Seen UTC", "Last Seen UTC", "Email Status", "Email Sent UTC", "Review Notes",
           "Contractor Phone", "Source Contact Name", "Source Contact Email", "Source Contact Phone",
           "Source Contact Role", "Source Contact Evidence"]
KEYS = ["source", "source_id", "source_url", "title", "stage", "deadline", "contractor",
        "contractor_email", "contact_evidence", "description", "first_seen", "last_seen",
        "email_status", "email_sent", "review_notes", "contractor_phone", "source_contact_name",
        "source_contact_email", "source_contact_phone", "source_contact_role", "source_contact_evidence"]
LAST_COL = "U"


def _service():
    if not (os.getenv("GOOGLE_SHEETS_ID") and os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")):
        raise RuntimeError("Google Sheets credentials are required for the free persistent project ledger")
    return _get_sheets_service(), os.environ["GOOGLE_SHEETS_ID"]


def _ensure_tab(service, sheet_id):
    meta = service.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
    if TAB not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        service.spreadsheets().batchUpdate(spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": TAB}}}]}).execute()
    header = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{TAB}'!A1:{LAST_COL}1").execute().get("values", [])
    if not header:
        service.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"'{TAB}'!A1:{LAST_COL}1",
            valueInputOption="RAW", body={"values": [HEADERS]}).execute()
    elif header[0] == HEADERS[:15]:
        service.spreadsheets().values().update(spreadsheetId=sheet_id, range=f"'{TAB}'!P1:{LAST_COL}1",
            valueInputOption="RAW", body={"values": [HEADERS[15:]]}).execute()
    elif header[0] != HEADERS:
        raise RuntimeError("Projects tab headers differ from expected schema; refusing to overwrite data")


def _rows(service, sheet_id):
    values = service.spreadsheets().values().get(spreadsheetId=sheet_id,
        range=f"'{TAB}'!A2:{LAST_COL}10000").execute().get("values", [])
    return [dict(zip(KEYS, row + [""] * (len(KEYS) - len(row)))) for row in values]


def read_projects(private=False):
    service, sheet_id = _service()
    _ensure_tab(service, sheet_id)
    rows = _rows(service, sheet_id)
    if private:
        return rows
    return [{k: v for k, v in row.items() if k not in ("review_notes",)}
            for row in rows]


def upsert_projects(items):
    items = list({(item["source"], item["source_id"]): item for item in items
                  if item.get("source") and item.get("source_id") and item.get("source_url") and item.get("title")}.values())
    if not items:
        return 0
    service, sheet_id = _service()
    _ensure_tab(service, sheet_id)
    existing = _rows(service, sheet_id)
    index = {(r["source"], r["source_id"]): (i + 2, r) for i, r in enumerate(existing) if r["source"] and r["source_id"]}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updates, additions = [], []
    for item in items:
        key = (item["source"], item["source_id"])
        position, previous = index.get(key, (None, {}))
        record = {k: previous.get(k, "") for k in KEYS}
        for key_name in KEYS[:10] + KEYS[15:]:
            if item.get(key_name):
                record[key_name] = str(item[key_name])
        record["first_seen"] = record["first_seen"] or now
        record["last_seen"] = now
        values = [record[k] for k in KEYS]
        if position:
            updates.append({"range": f"'{TAB}'!A{position}:{LAST_COL}{position}", "values": [values]})
        else:
            additions.append(values)
    if updates:
        service.spreadsheets().values().batchUpdate(spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates}).execute()
    if additions:
        service.spreadsheets().values().append(spreadsheetId=sheet_id, range=f"'{TAB}'!A:{LAST_COL}",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": additions}).execute()
    log.info("Project ledger: %d updates, %d additions", len(updates), len(additions))
    return len(items)


def claim_approved_contact():
    """Move one reviewed row to SENDING before n8n drafts or sends."""
    service, sheet_id = _service()
    _ensure_tab(service, sheet_id)
    for row_number, row in enumerate(_rows(service, sheet_id), start=2):
        if row["email_status"].strip().upper() != "APPROVED" or row["stage"] == "historical":
            continue
        if not (row["contractor"].strip() and row["contact_evidence"].startswith("https://") and
                re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["contractor_email"].strip())):
            continue
        service.spreadsheets().values().update(spreadsheetId=sheet_id,
            range=f"'{TAB}'!M{row_number}", valueInputOption="RAW",
            body={"values": [["SENDING"]]}).execute()
        return {"row_number": row_number, **row, "email_status": "SENDING"}
    return None


def mark_sent(row_number, source, source_id, recipient, message_id):
    service, sheet_id = _service()
    _ensure_tab(service, sheet_id)
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id,
        range=f"'{TAB}'!A{row_number}:O{row_number}").execute().get("values", [])
    if not result:
        raise ValueError("Project row no longer exists")
    row = dict(zip(KEYS, result[0] + [""] * (len(KEYS) - len(result[0]))))
    if (row["source"], row["source_id"], row["contractor_email"], row["email_status"]) != (source, source_id, recipient, "SENDING"):
        raise ValueError("Row, recipient or status changed; email log needs manual review")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    service.spreadsheets().values().update(spreadsheetId=sheet_id,
        range=f"'{TAB}'!M{row_number}:O{row_number}", valueInputOption="RAW",
        body={"values": [["SENT", now, f"Gmail message ID: {message_id}"]]}).execute()
    return {"status": "SENT", "sent_at": now}
