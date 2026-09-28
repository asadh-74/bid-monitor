"""Evidence-gated drafts and explicitly approved SMTP delivery."""
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import select, update

from .store import Outreach, Project, session


async def draft_for(project_id: int):
    with session() as db:
        project = db.get(Project, project_id)
        if not project:
            raise ValueError("Project not found")
        if not (project.contractor and project.contractor_email and project.contact_evidence):
            raise ValueError("A named contractor, matching email and contact evidence are required")
        if db.scalar(select(Outreach).where(Outreach.project_id == project_id, Outreach.recipient == project.contractor_email)):
            raise ValueError("An outreach record already exists for this project and recipient")
        snapshot = {"title": project.title, "contractor": project.contractor, "email": project.contractor_email,
                    "source": project.source_url, "stage": project.stage}

    subject = f"Regarding {snapshot['title']}"
    body = (f"Hello {snapshot['contractor']} team,\n\n"
            f"I came across {snapshot['title']} ({snapshot['stage']}) at {snapshot['source']}. "
            "I would like to discuss whether our services may be relevant. "
            "Would you be open to a brief conversation?\n\nBest regards,\n")
    key = os.getenv("OPENAI_API_KEY")
    if key:
        prompt = ("Draft a concise professional outreach email. Use only these verified facts; no invented "
                  f"claims, qualifications, dates, or contacts: {snapshot}. Return JSON with subject and body. "
                  "Leave sender signature blank.\n")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"}, json={"model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
            response.raise_for_status()
            import json
            result = json.loads(response.json()["choices"][0]["message"]["content"])
            subject, body = result["subject"], result["body"]
    with session() as db:
        draft = Outreach(project_id=project_id, recipient=snapshot["email"], subject=subject, body=body)
        db.add(draft)
        db.commit()
        return draft.id


def send_approved(draft_id: int):
    with session() as db:
        if not all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")):
            raise ValueError("SMTP configuration is incomplete")
        claimed = db.execute(update(Outreach).where(Outreach.id == draft_id, Outreach.approved.is_(True),
                         Outreach.status == "approved").values(status="sending"))
        if claimed.rowcount != 1:
            db.rollback()
            raise ValueError("Draft must be approved and unsent")
        db.commit()
        draft = db.get(Outreach, draft_id)
        msg = EmailMessage()
        msg["From"] = os.environ["MAIL_FROM"]
        msg["To"] = draft.recipient
        msg["Subject"] = draft.subject
        msg.set_content(draft.body)
        try:
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=30) as smtp:
                smtp.starttls()
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                smtp.send_message(msg)
        except Exception as exc:
            # SMTP can accept a message before a connection error. Never retry blindly.
            draft.status = "delivery_uncertain"
            draft.error = str(exc)[:1000]
            db.commit()
            raise
        draft.status = "sent"
        draft.sent_at = datetime.now(timezone.utc)
        db.commit()
