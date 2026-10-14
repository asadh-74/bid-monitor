# Bid Monitor

A construction bid monitoring application built during my work as an **Automation Engineer at CYMATICS TECHNOLOGIES**. It collects opportunities from multiple sources, organizes project and contact data in Google Sheets, and serves a searchable FastAPI dashboard.

**[Live dashboard](https://bid-monitor.onrender.com/)** · **[Collection workflow](https://github.com/asadh-74/bid-monitor/actions/workflows/collect-bids.yml)**

## Features

- Unified project records with source IDs, URLs, deadlines, first-seen and last-seen timestamps.
- Search and filters for source, named contractors/participants, and evidenced contractor email.
- Published bid, award, estimate and budget values with their source labels.
- Separate general contractor, architect/engineer, agency contact and participant roles.
- Linked contact evidence; missing details remain unlisted.
- Deduplication by source and project ID, preserving outreach history.
- Organized **Client Projects** and **Project Contacts** views derived from the master **Projects** ledger.
- Scheduled GitHub Actions collection with independent source error handling.
- n8n workflow for approved contacts, AI-assisted drafting, Gmail delivery and delivery logging.

The current public view is restricted to projects supported by **2026 source dates**. Scrape timestamps and solicitation numbers alone do not establish the project year.

## Architecture

GitHub Actions runs the collectors and writes to the existing Google Sheet. Render serves the dashboard and APIs from that ledger. n8n handles reviewed contractor outreach.

| Component | Technology |
| --- | --- |
| API and dashboard | Python, FastAPI, Uvicorn, HTML/CSS/JavaScript |
| Browser-based collection | Playwright and Chromium |
| HTML parsing and requests | Beautiful Soup, HTTPX |
| PDF extraction | PyMuPDF |
| Durable ledger and client exports | Google Sheets API, Google service-account authentication |
| Scheduling and hosting | GitHub Actions, Render |
| Outreach integration | n8n, Gemini and Gmail |

Google Sheets is the current durable store; this version does not use a separate relational database. Free Render restarts do not erase sheet records.

## Source coverage

| Source | Implementation and interpretation |
| --- | --- |
| DASNY construction contracts | Public listings and detail collection; agency contacts remain separate from contractors. |
| NYS Contract Reporter | Paginated construction notices; configurable agency filter. |
| NYC SCA advertised/limited bids | SCA InfoHub tables, including procurement specialist contacts. |
| NYC SCA anticipated contracts | Official NYC Open Data CIP/capacity datasets; anticipated projects are not presented as awarded contracts. |
| NYC SCA factsheets | Official project PDFs; contractor/architect and contract values when published. Occupancy year establishes eligibility for the current view. |
| Total Bid Data | Authorized login; bidding, follow-up, bid-result and award queues; role-labelled company/contact details. |
| MyVendorLink | Authorized login; active listings, detail pages and linked company profiles. |
| NYC SCA BidSet | **Snapshot import:** 289 unique records with 2026 opening dates imported on October 9, 2026. Published ranges and specialist contacts included. Scheduled BidSet collection and document-based contractor/engineer enrichment are not implemented yet. |
| Construction.com / Dodge | Not integrated; requires an authorized project API or licensed feed. |

Planholders and bidders are participants, not confirmed awarded general contractors. A published range is not an actual submitted bid. Procurement specialists are not contractor recipients.

## Setup

### Google Sheets

Share the existing spreadsheet with the service account as an Editor. The app uses:

- **Projects:** master ledger, deduplication, contact evidence and outreach state.
- **Client Projects:** organized client-facing project view.
- **Project Contacts:** role-labelled contact view.

Other legacy tabs are preserved. Generated client views are rebuilt after collection; edit the master ledger rather than relying on manual edits in generated views.

### GitHub Actions secrets

Configure these in **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
| --- | --- |
| `GOOGLE_SHEETS_ID` | Existing spreadsheet ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service-account JSON |
| `TOTALBIDDATA_USERNAME` | Authorized Total Bid Data account |
| `TOTALBIDDATA_PASSWORD` | Total Bid Data password |
| `MYVENDORLINK_EMAIL` | Authorized MyVendorLink account |
| `MYVENDORLINK_PASSWORD` | MyVendorLink password |

The **Collect bids** workflow runs at minutes **17 and 47** each hour, supports manual dispatch, and runs when relevant collector code is pushed to `main`. GitHub schedules may be delayed. A concurrency group prevents overlapping collection jobs; jobs have a 90-minute timeout.

Each private source processes a bounded detail batch, prioritizing missing records and then older records. Large backfills can require multiple runs. Inspect workflow logs for limits, warnings and failed collectors; a successful dashboard load does not prove every source was collected.

### Render environment

Set privately:

- `GOOGLE_SHEETS_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `ADMIN_TOKEN` — a long, random secret for protected APIs.
- `ENABLE_LOCAL_SCRAPING=false`

Render is configured as a dashboard service. Startup scraping and `POST /run` are disabled by default. Keep the old n8n Render scraping trigger inactive. Render environment variables do not automatically become GitHub Actions secrets.

The dashboard refreshes its data every minute. Free Render may take time to start after inactivity.

### Local development

```bash
python -m venv .venv
# Activate the virtual environment for your operating system.
pip install -r requirements.txt
python -m playwright install chromium

# Configure environment variables privately using env.example as a reference.
uvicorn app.main:app --reload
```

Run the external collector manually with the required secrets:

```bash
python -m app.collect
```

Run the existing test suite:

```bash
python -m unittest discover -s tests -v
```

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Searchable dashboard |
| GET | `/health` | FastAPI process health |
| GET | `/api/projects?source=dasny&limit=1000` | Public 2026 project feed; maximum limit 5,000 |
| GET | `/api/bids` | SCA factsheet and advertised/limited compatibility feed |
| GET | `/api/status` | Collection mode and in-process status |
| GET | `/api/projects/private` | Private ledger feed; admin token required |
| POST | `/api/outreach/claim` | Claim one approved outreach record |
| POST | `/api/outreach/sent` | Record delivery and message ID |
| POST | `/run` | Local collection only when explicitly enabled; admin token required |

Protected endpoints require the `X-Admin-Token` header. In dashboard mode, `/api/status` reports external collection; **GitHub Actions logs are the source of scheduled-run status**. `/health` does not verify collection freshness or email delivery.

## Contractor outreach

Import [the contractor email workflow](n8n/contractor_email_workflow.json) into n8n. Configure Header Auth, Gemini and Gmail credentials privately, and replace the company/offer placeholder in the AI prompt.

1. Confirm the named contractor and exact email against contact evidence.
2. Review the intended outreach and mark **Email Status** as `APPROVED`.
3. n8n claims the record as `SENDING` before drafting and sending.
4. AI drafts from stored project facts and the approved company offering.
5. Gmail sends to the approved recipient; the API logs `SENT`, timestamp and message ID.

Test with your own approved recipient before activating the schedule. A row left as `SENDING` requires investigation: check Gmail before retrying, particularly if delivery succeeded but logging failed. Do not automatically email a company inferred only from its name.

The repository includes the integration workflow, not sender credentials. Email delivery depends on the separately configured n8n instance.

## Data quality and limitations

- Source layouts, availability and authenticated sessions can change.
- Missing amounts, contractor names, engineer names or emails are never invented.
- Historical and undated rows may remain in the ledger for history but are excluded from the public 2026 view.
- Twelve previously inspected SCA factsheets had 2020–2021 occupancy dates and were excluded by the year rule; this is distinct from a parsing failure.
- BidSet document enrichment and automatic BidSet refresh remain future work.
- External website data-entry agents are a future phase, not part of the current application.
- The application does not submit bids or modify source-site contracts.

## Credential handling

Keep passwords, API keys, admin tokens, service-account keys and browser sessions out of commits, screenshots and workflow exports. Use repository secrets, Render environment variables and n8n credentials. Public examples should contain placeholders only.

## Author

**Asad Hussain** — Automation Engineer  
[GitHub](https://github.com/asadh-74)
