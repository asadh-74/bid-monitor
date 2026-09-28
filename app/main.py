"""Free-tier bid monitor: source collectors -> one Google Sheet -> dashboard/n8n."""
import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .pdf_parser import fetch_and_parse_factsheet
from .project_store import claim_approved_contact, mark_sent, read_projects, upsert_projects
from .public_sources import collect_awards, collect_dasny, collect_nyscr
from .renderer import get_renderer
from .scainfohub_parser import parse_scainfohub_table
from .scraper import discover_factsheet_pdfs
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bid-monitor")
app = FastAPI(title="Bid Monitor")
run_lock = asyncio.Lock()
outreach_lock = asyncio.Lock()
last_run = {}
SCAINFOHUB_URLS = {
    "advertised": "https://scainfohub.azurewebsites.net/advertised-bids",
    "limited": "https://scainfohub.azurewebsites.net/limited-bids",
}


def require_admin(x_admin_token: str | None = Header(default=None)):
    token = os.getenv("ADMIN_TOKEN")
    if not token or not x_admin_token or not secrets.compare_digest(token, x_admin_token):
        raise HTTPException(401, "Admin token required")


async def run_factsheets():
    discovered = await discover_factsheet_pdfs()
    items = []
    for found in discovered:
        data = await fetch_and_parse_factsheet(found.pdf_url, project_id_hint=found.project_name_hint)
        if data:
            stage = "in_construction"
            try:
                occupancy = datetime.strptime(data.occupancy_date.strip(), "%B %Y").replace(tzinfo=timezone.utc)
                if occupancy < datetime.now(timezone.utc):
                    stage = "historical"
            except ValueError:
                stage = "unknown"
            items.append(dict(source="sca_factsheet", source_id=data.project_id,
                              source_url=data.source_pdf_url, title=data.project_name,
                              stage=stage, contractor=data.general_contractor,
                              architect_engineer=data.architect_engineer,
                              description=f"Type: {data.project_type}; Location: {data.location}; Category: {found.category}"))
        await asyncio.sleep(0.5)
    return {"discovered": len(discovered), "valid": len(items), "stored": upsert_projects(items)}


async def run_scainfohub():
    renderer = get_renderer()
    summary = {}
    for source, url in SCAINFOHUB_URLS.items():
        html = await renderer.render(url, wait_seconds=4)
        rows = parse_scainfohub_table(html, source=source)
        items = [dict(source=f"sca_{source}", source_id=row.solicitation_number,
                      source_url=url, title=row.school_description, stage="advertised",
                      deadline=row.bid_open_date, description=f"Contract type: {row.contract_type}; documents available: {row.doc_avail_date}; pre-bid: {row.pre_bid_meeting_date}",
                      source_contact_name=row.specialist_name, source_contact_email=row.specialist_email,
                      source_contact_phone=row.specialist_phone, source_contact_role="SCA bid specialist",
                      source_contact_evidence=url)
                 for row in rows if row.solicitation_number and row.school_description]
        summary[source] = {"parsed": len(rows), "stored": upsert_projects(items)}
    return summary


async def run_full_scrape():
    global last_run
    if run_lock.locked():
        return {"status": "already_running"}
    async with run_lock:
        summary = {}
        last_run = {"status": "running"}
        for name, collector in (("dasny", collect_dasny), ("nyscr", collect_nyscr),
                                ("sca_awards", collect_awards), ("sca_bids", run_scainfohub),
                                ("sca_factsheets", run_factsheets)):
            try:
                result = await collector()
                summary[name] = upsert_projects(result) if isinstance(result, list) else result
            except Exception as exc:
                log.exception("Collector %s failed", name)
                summary[name] = {"error": str(exc)}
            last_run = {"status": "running", **summary}
        last_run = {"status": "complete", **summary}
        log.info("Collection complete: %s", summary)
        return summary


@app.post("/run", dependencies=[Depends(require_admin)])
async def trigger_run(background_tasks: BackgroundTasks):
    if run_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(run_full_scrape)
    return {"status": "started"}


@app.get("/api/projects")
async def api_projects(source: str | None = None, limit: int = 1000):
    rows = read_projects()
    if source:
        rows = [row for row in rows if row["source"] == source]
    return rows[:min(max(limit, 1), 5000)]


@app.get("/api/projects/private", dependencies=[Depends(require_admin)])
async def api_private_projects(source: str | None = None):
    """n8n may read verified contractor contacts and email status from the ledger."""
    rows = read_projects(private=True)
    return [row for row in rows if row["source"] == source] if source else rows


@app.post("/api/outreach/claim", dependencies=[Depends(require_admin)])
async def claim_outreach():
    async with outreach_lock:
        return {"project": claim_approved_contact()}


class SentMessage(BaseModel):
    row_number: int
    source: str
    source_id: str
    recipient: str
    message_id: str


@app.post("/api/outreach/sent", dependencies=[Depends(require_admin)])
async def log_sent(message: SentMessage):
    if not message.message_id.strip() or message.row_number < 2:
        raise HTTPException(422, "Gmail message ID and valid row required")
    try:
        return mark_sent(message.row_number, message.source, message.source_id,
                         message.recipient, message.message_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/bids")
async def legacy_bids():
    rows = read_projects()
    return {"factsheets": [r for r in rows if r["source"] == "sca_factsheet"],
            "advertised_limited_bids": [r for r in rows if r["source"] in ("sca_advertised", "sca_limited")]}


@app.get("/api/status")
async def status():
    return {"running": run_lock.locked(), "last_run": last_run}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bid Monitor</title><style>body{font:15px system-ui,sans-serif;margin:0;background:#f7f9fc;color:#172235}header{background:#102343;color:white;padding:24px}main{padding:24px;max-width:1500px;margin:auto}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}select,input{padding:10px;border:1px solid #bbc7d6;border-radius:6px}.wrap{overflow:auto;background:white;border:1px solid #dce3ec;border-radius:8px}table{border-collapse:collapse;width:100%;min-width:950px}th,td{padding:10px;text-align:left;border-bottom:1px solid #e5eaf0;vertical-align:top}th{background:#eaf0f7;position:sticky;top:0}a{color:#0758a5}small{color:#637185}</style></head>
<body><header><h1>Bid Monitor</h1><span>Public construction opportunities and project factsheets</span></header><main>
<div class="controls"><label>Source <select id="source"><option value="">All sources</option></select></label><label>Search <input id="search" placeholder="Project, contractor, ID"></label></div>
<p id="status">Loading projects…</p><div class="wrap"><table><thead><tr><th>Source</th><th>Project</th><th>Stage</th><th>Deadline</th><th>General contractor</th><th>Architect / engineer</th><th>Contractor contact</th><th>Source contact</th><th>First seen</th><th>Source link</th></tr></thead><tbody id="rows"></tbody></table></div><p><small>Bid advertisements often precede contractor selection. SCA factsheets list general contractor and architect/engineer when awarded. Source contacts may be agency bid specialists.</small></p></main>
<script>
let all=[];const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function show(){const source=document.getElementById('source').value,q=document.getElementById('search').value.toLowerCase();const items=all.filter(r=>(!source||r.source===source)&&[r.title,r.contractor,r.architect_engineer,r.source_id,r.source_contact_name].join(' ').toLowerCase().includes(q));document.getElementById('status').textContent=items.length+' projects shown · '+all.length+' total';document.getElementById('rows').innerHTML=items.map(r=>'<tr><td>'+esc(r.source)+'</td><td><strong>'+esc(r.title)+'</strong><br><small>'+esc(r.source_id)+'</small></td><td>'+esc(r.stage)+'</td><td>'+esc(r.deadline)+'</td><td>'+esc(r.contractor||'Not listed')+'</td><td>'+esc(r.architect_engineer||'Not listed')+'</td><td>'+esc(r.contractor_email||'Email not verified')+'<br>'+esc(r.contractor_phone||'Phone not verified')+(r.contact_evidence?'<br><a href="'+esc(r.contact_evidence)+'" target="_blank" rel="noopener noreferrer">Contact evidence</a>':'')+'</td><td>'+esc(r.source_contact_name||'Not listed')+'<br>'+esc(r.source_contact_role)+(r.source_contact_email?'<br>'+esc(r.source_contact_email):'')+(r.source_contact_phone?'<br>'+esc(r.source_contact_phone):'')+'</td><td>'+esc(r.first_seen)+'</td><td><a href="'+esc(r.source_url)+'" target="_blank" rel="noopener noreferrer">View source</a></td></tr>').join('')||'<tr><td colspan="10">No matching projects</td></tr>'}
async function load(){try{let res=await fetch('/api/projects?limit=5000');if(!res.ok)throw Error('HTTP '+res.status);all=await res.json();const selected=document.getElementById('source').value;document.getElementById('source').innerHTML='<option value="">All sources</option>'+[...new Set(all.map(r=>r.source))].sort().map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join('');document.getElementById('source').value=selected;show()}catch(e){document.getElementById('status').textContent='Could not load projects: '+e}}
document.getElementById('source').addEventListener('change',show);document.getElementById('search').addEventListener('input',show);load();setInterval(load,60000);
</script></body></html>"""


@app.on_event("startup")
async def start_scheduler():
    interval = float(os.getenv("SCRAPE_INTERVAL_HOURS", "12"))
    if interval > 0:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(run_full_scrape, "interval", hours=interval, max_instances=1)
        scheduler.start()
        asyncio.create_task(run_full_scrape())
