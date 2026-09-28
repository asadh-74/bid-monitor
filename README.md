# Bid Monitor

FastAPI dashboard and scheduled collectors for public construction projects. PostgreSQL is the durable record; Google Sheets is an optional export of the existing SCA feeds.

## Source coverage

| Source | Status | Notes |
| --- | --- | --- |
| SCA factsheets: New Schools and Projects in Construction | Implemented | Playwright discovers PDFs and extracts project and contractor. |
| SCA advertised and limited bids | Implemented | scainfohub tables; bid specialist is not a contractor contact. |
| DASNY construction contracts | Added, requires live Render verification | Parses listings; rejects missing title or solicitation. |
| NYS Contract Reporter | Partial | Parses the first search results page. Configure `NYSCR_AGENCY_FILTER`; pagination and agency search need validation. |
| SCA anticipated awards | Experimental | Reports an error if a contract table cannot be verified. |
| Construction.com | Not integrated | Requires an authorized project feed or API, not its homepage. |

Each collector fails independently. `GET /api/projects` serves the latest 100 records by default, supports `source` and `limit` (maximum 1000), and excludes contact email and evidence. A failed collector does not create guessed data.

## Render setup

Create a Blueprint from this repository. It provisions a Starter web service and persistent PostgreSQL database. Set `ADMIN_TOKEN` to a long random secret. Optional variables: `NYSCR_AGENCY_FILTER`, `GOOGLE_SHEETS_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`. Keep secrets in Render environment variables. `SCRAPE_INTERVAL_HOURS` defaults to 12; 0 disables automatic runs. The service initializes tables and starts collection on startup.

- `GET /` dashboard
- `GET /api/projects?source=dasny&limit=100` unified project feed
- `GET /api/bids` legacy SCA-only JSON
- `GET /health` process check (not a freshness guarantee)
- `POST /run` all collectors, `POST /run/factsheets` and `POST /run/scainfohub` targeted runs; all require `X-Admin-Token`

A successful deployment and source-specific run are necessary before claiming live coverage. Repository changes alone do not deploy.

## Contractor outreach

Outreach is off by default. A factsheet may name a contractor without giving an email. A bid specialist email belongs to the procurement office and is never used as a contractor recipient.

1. Manually confirm a public email belongs to the named contractor. Record contractor, email, and public `evidence_url` with `PUT /api/projects/{id}/contact`. The endpoint records your assertion; you must check its evidence.
2. `POST /api/projects/{id}/draft` creates a draft using `OPENAI_API_KEY` if configured, or a plain template otherwise. It does not send.
3. `GET /api/outreach` shows recipient and draft for review. `PUT /api/outreach/{id}` edits its subject and body, resetting approval.
4. `POST /api/outreach/{id}/approve`, then separately `POST /api/outreach/{id}/send`. Delivery requires SMTP credentials. A delivery error is marked `delivery_uncertain` and must be checked manually before retrying.

All outreach routes require `X-Admin-Token: <ADMIN_TOKEN>`. Never put that token in a public frontend. These are API endpoints; an authenticated review interface remains to be built for easier operator use.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# Set DATABASE_URL to a local PostgreSQL database and ADMIN_TOKEN to a secret
uvicorn app.main:app --reload
```

SQLite may be used for isolated local tests (`DATABASE_URL=sqlite:////tmp/bid-monitor-test.db`). Use PostgreSQL on Render because web-service filesystem data is ephemeral.
