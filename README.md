# SCA Factsheet + Live Bid Scraper (Playwright)

Real headless-browser rendering for the two NYC SCA sources that a plain
HTTP fetch can never read: the project factsheet PDFs (linked from a
JavaScript-templated page) and the Advertised/Limited Bids tables on
scainfohub.azurewebsites.net.

## What this replaces in your n8n workflow

n8n's Browserless-based approximation (`D4b`/`D4c`/`D4d`, and the SCA
Advertised/Limited Bids branches `B2a/B2b/B3a/B3b`, which only ever logged
"needs Playwright" and never produced real data) are now redundant - this
service does that job properly, with a real browser, no third-party
rendering API to configure or pay for. Those nodes should be removed from
n8n (see the updated workflow file).

## Setup

1. **Google service account** (same one n8n's Google Sheets nodes use, or
   a new one - either works):
   - Google Cloud Console → APIs & Services → Credentials → Create service account
   - Enable the Google Sheets API for the project
   - Create a JSON key, download it
   - Share your Google Sheet with the service account's email address (found
     inside the JSON key) as an Editor
   - Paste the entire JSON content as the `GOOGLE_SERVICE_ACCOUNT_JSON`
     environment variable (as one line - most platforms handle multi-line
     JSON fine in an env var, but if yours doesn't, minify it first)

2. **Sheet tabs** - this service creates its own two tabs automatically on
   first write if they don't exist: `SCA Factsheets` and
   `SCA Advertised-Limited Bids`. No manual setup needed beyond sharing
   the sheet with the service account.

3. **Local test run:**
   ```bash
   pip install -r requirements.txt --break-system-packages
   playwright install --with-deps chromium
   export GOOGLE_SHEETS_ID=your_sheet_id
   export GOOGLE_SERVICE_ACCOUNT_JSON='{"type": "service_account", ...}'
   uvicorn app.main:app --reload
   ```
   Then visit `http://localhost:8000` for the live dashboard, or
   `curl -X POST http://localhost:8000/run` to trigger a scrape manually.

## Deploying to Render

1. Push this folder to a GitHub repo.
2. In Render: **New → Blueprint**, point it at the repo (it reads
   `render.yaml` automatically).
3. Render will prompt for the two secret env vars (`GOOGLE_SHEETS_ID`,
   `GOOGLE_SERVICE_ACCOUNT_JSON`) since they're marked `sync: false` -
   paste them in when asked.
4. Deploy. Render builds the Docker image (Playwright's official base
   image, so no manual browser-install step needed) and starts the service.
5. Your live dashboard is at `https://<your-render-app>.onrender.com/`.
   Point your actual domain (accuratebidservices.com or a subdomain of it)
   at this Render service via a CNAME, or have your existing website's
   frontend fetch `https://<your-render-app>.onrender.com/api/bids`
   directly and render its own table - whichever fits your site's current
   setup better.

## Endpoints

- `GET /` - the live dashboard (auto-refreshes every 60 seconds)
- `GET /api/bids` - live JSON: `{ factsheets: [...], advertised_limited_bids: [...] }`
- `POST /run` - manually trigger a full scrape (both factsheets and scainfohub)
- `POST /run/factsheets` - factsheets only
- `POST /run/scainfohub` - scainfohub bid tables only
- `GET /health` - health check

By default it also re-scrapes automatically every `SCRAPE_INTERVAL_HOURS`
(12 by default) - adjust that env var, or set it to `0` and trigger runs
externally instead (a Render cron job, GitHub Actions schedule, or an n8n
Schedule Trigger hitting `POST /run` - n8n is still useful as the
scheduler/glue layer even though it's no longer doing the rendering itself).

## Known limitation, stated plainly

`scraper.py`'s `CATEGORY_HASHES` list (currently "New-Schools-31" and
"Projects-in-Construction-37") is the set of category hashes confirmed
from this project's research so far. If NYCSCA's Projects page has more
categories than that, they won't be discovered automatically - add their
hashes to that list. There's no way to enumerate the full category list
without first rendering the page's top-level navigation, which this
deliberately does not attempt blindly (matches the whole approach in this
project: confirm real structure before writing a parser for it, rather
than guessing at more selectors).
