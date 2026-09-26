import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .pdf_parser import fetch_and_parse_factsheet
from .renderer import get_renderer
from .scainfohub_parser import parse_scainfohub_table
from .scraper import discover_factsheet_pdfs
from .sheets_writer import read_all_live_data, write_factsheets, write_sca_bids

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sca-scraper")

app = FastAPI(title="SCA Factsheet + Bids Scraper")

SCAINFOHUB_URLS = {
    "advertised": "https://scainfohub.azurewebsites.net/advertised-bids",
    "limited": "https://scainfohub.azurewebsites.net/limited-bids",
}


async def run_factsheet_scrape() -> dict:
    logger.info("Starting factsheet discovery run")
    discovered = await discover_factsheet_pdfs()
    logger.info("Discovered %d factsheet PDF links total", len(discovered))

    results, failures = [], []
    for item in discovered:
        data = await fetch_and_parse_factsheet(item.pdf_url, project_id_hint=item.project_name_hint)
        (results if data else failures).append(data or item.pdf_url)
        await asyncio.sleep(1.0)  # be polite to the PDF host

    write_factsheets([r for r in results if r])
    return {"discovered": len(discovered), "parsed": len(results), "failed": len(failures)}


async def run_scainfohub_scrape() -> dict:
    logger.info("Starting scainfohub bid tables scrape")
    renderer = get_renderer()
    all_rows = []
    per_source = {}

    for source, url in SCAINFOHUB_URLS.items():
        try:
            html = await renderer.render(url, wait_seconds=4.0)
        except Exception as e:
            logger.error("Failed to render %s: %s", url, e)
            per_source[source] = 0
            continue
        rows = parse_scainfohub_table(html, source=source)
        per_source[source] = len(rows)
        all_rows.extend(rows)

    write_sca_bids(all_rows)
    return {"per_source": per_source, "total_rows": len(all_rows)}


async def run_full_scrape() -> dict:
    factsheets_summary = await run_factsheet_scrape()
    bids_summary = await run_scainfohub_scrape()
    summary = {"factsheets": factsheets_summary, "scainfohub_bids": bids_summary}
    logger.info("Full run complete: %s", summary)
    return summary


@app.post("/run")
async def trigger_run():
    """Runs everything: factsheets + scainfohub bid tables."""
    return await run_full_scrape()


@app.post("/run/factsheets")
async def trigger_factsheets():
    return await run_factsheet_scrape()


@app.post("/run/scainfohub")
async def trigger_scainfohub():
    return await run_scainfohub_scrape()


@app.get("/api/bids")
async def api_bids():
    """Live JSON data for the client-facing website to consume directly -
    this is what makes the data appear 'live on the website' instead of
    only living in the Google Sheet."""
    return read_all_live_data()


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """A simple, self-contained live dashboard. Point your real website's
    domain at this service (or embed this page in an iframe, or have your
    website's own frontend call /api/bids directly) - either works."""
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NYC SCA Live Bid Data</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 24px; background: Canvas; color: CanvasText; }
  h1 { font-size: 1.4rem; }
  h2 { font-size: 1.1rem; margin-top: 2rem; }
  table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; font-size: 0.85rem; }
  th, td { border: 1px solid #8888; padding: 6px 8px; text-align: left; vertical-align: top; }
  th { background: #8882; position: sticky; top: 0; }
  .updated { color: #888; font-size: 0.8rem; }
  .wrap { overflow-x: auto; }
</style>
</head>
<body>
  <h1>NYC SCA Live Bid Data</h1>
  <div class="updated" id="updated">Loading...</div>

  <h2>Advertised / Limited Bids</h2>
  <div class="wrap"><table id="bidsTable"><thead></thead><tbody></tbody></table></div>

  <h2>Project Factsheets (Contractor Info)</h2>
  <div class="wrap"><table id="factsheetsTable"><thead></thead><tbody></tbody></table></div>

<script>
function renderTable(tableId, rows) {
  const table = document.getElementById(tableId);
  if (!rows.length) { table.querySelector('tbody').innerHTML = '<tr><td>No data yet</td></tr>'; return; }
  const headers = Object.keys(rows[0]);
  table.querySelector('thead').innerHTML = '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';
  table.querySelector('tbody').innerHTML = rows.map(r =>
    '<tr>' + headers.map(h => `<td>${(r[h] || '').toString().replace(/</g,'&lt;')}</td>`).join('') + '</tr>'
  ).join('');
}

async function refresh() {
  try {
    const res = await fetch('/api/bids');
    const data = await res.json();
    renderTable('bidsTable', data.advertised_limited_bids || []);
    renderTable('factsheetsTable', data.factsheets || []);
    document.getElementById('updated').textContent = 'Last refreshed: ' + new Date().toLocaleString();
  } catch (e) {
    document.getElementById('updated').textContent = 'Failed to load data - ' + e;
  }
}

refresh();
setInterval(refresh, 60000); // auto-refresh every 60 seconds
</script>
</body>
</html>
"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def start_scheduler():
    interval_hours = float(os.getenv("SCRAPE_INTERVAL_HOURS", "12"))
    if interval_hours <= 0:
        logger.info("SCRAPE_INTERVAL_HOURS <= 0 - automatic scheduling disabled, use POST /run manually.")
        return
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_full_scrape, "interval", hours=interval_hours)
    scheduler.start()
    logger.info("Scheduled automatic runs every %s hours.", interval_hours)
