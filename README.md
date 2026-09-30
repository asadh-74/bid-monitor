# Bid Monitor (free Render + Google Sheets)

The Render app collects public opportunities into one `Projects` tab in the **existing Google Sheet**. The sheet is durable when the free web service sleeps or restarts. `/api/projects` and the dashboard read that tab. n8n owns contact review, AI drafting, Gmail delivery, and writing email status back to the sheet.

## Sources

| Collector | Coverage |
| --- | --- |
| SCA project factsheets | New Schools and Projects in Construction categories; only project PDFs with confirmed project fields are accepted. Historical occupancy dates are marked `historical`. |
| SCA advertised and limited bids | scainfohub tables; specialist contact is a procurement contact, **not** a contractor recipient. |
| DASNY construction contracts | Listings and pagination from the construction contracts source. |
| NYS Contract Reporter | Paginated open construction notices (up to `NYSCR_MAX_PAGES`, default 40). The optional `NYSCR_AGENCY_FILTER` narrows the agency; blank means statewide construction. |
| SCA anticipated CIP and capacity contracts | NYC Open Data's official public datasets `tsak-vtv3` and `6m3u-8rbh`. Dates are left blank when the dataset omits them. These are **anticipated**, not awarded. |
| Total Bid Data | Authenticated bidding, follow-up, bid-result, and awarded queues. Maps contract and role-labeled contacts to existing columns. |
| Construction.com / Dodge | Requires an authorized Dodge project API or licensed data feed; the marketing homepage has no downloadable project listing. |

Each collector fails independently and `/api/status` reports its most recent in-process results. `/health` only checks that FastAPI is up. The current site must be redeployed from this branch before new sources appear; repository code alone does not change the live service.

## Render configuration

Keep the existing `GOOGLE_SHEETS_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`. Set a long random `ADMIN_TOKEN` as a Render environment secret. The service account must have Editor access to the sheet. The app creates `Projects` with these columns: Source, Source ID, Source URL, Title, Stage, Deadline, Contractor, Contractor Email, Contact Evidence, Description, First Seen UTC, Last Seen UTC, Email Status, Email Sent UTC, Review Notes. Existing `Bids` and SCA tabs are not modified or deleted.

A free Render service may sleep between visits; the app starts a collection on wake and repeats it every `SCRAPE_INTERVAL_HOURS` while running. For reliable periodic collection even while it sleeps, schedule an n8n HTTP Request `POST https://bid-monitor.onrender.com/run` with `X-Admin-Token` in n8n's Header Auth credential. The endpoint returns `started` before a run completes; poll `/api/status` for per-source results. Avoid triggering overlapping runs.

Import [`n8n/render_trigger_workflow.json`](n8n/render_trigger_workflow.json) to replace the old three-node Render trigger. It starts inactive, has a manual test and six-hour schedule, warms `/health` with retries for free-tier 503 responses, posts `/run`, waits seven minutes, and checks `/api/status` for completion and source errors. Set `ADMIN_TOKEN` in Render and select an n8n **Header Auth** credential named `X-Admin-Token` with the same secret on **Start Collection**; the secret is not in the JSON. Test manually before publishing the schedule. Persistent 503s or a failed source still require Render log investigation. Keep the older Render trigger inactive to avoid duplicate schedules. Contractor email is a separate reviewed workflow below.

- `GET /` searchable, source-filterable dashboard
- `GET /api/projects?source=dasny&limit=1000` public feed without private contact columns
- `GET /api/projects/private` full ledger for n8n, requires `X-Admin-Token`
- `GET /api/bids` SCA-only compatibility feed with the new schema
- `GET /api/status` last in-process collector results
- `POST /run` starts collection, requires `X-Admin-Token`

## Contractor email with n8n

Import [`n8n/contractor_email_workflow.json`](n8n/contractor_email_workflow.json) as a **separate, inactive** workflow. Configure the Header Auth credential (`X-Admin-Token`) on both HTTP nodes, the Gemini credential, and Gmail credential. Replace `REPLACE_WITH_YOUR_COMPANY_AND_OFFERING` in the AI prompt. Test with your own approved test recipient before activating the hourly trigger. Never place the admin token in the workflow JSON or public dashboard.

Only mark a project `APPROVED` in **Email Status** after you confirm that **Contractor Email** belongs to the named **Contractor**, save the public proof URL in **Contact Evidence**, and review the intended outreach. Do not use the SCA procurement specialist's email as a contractor recipient. The n8n workflow:

1. Read the `Projects` sheet and take only rows with `Email Status = APPROVED`, a named contractor, a validated contractor email and evidence URL.
2. Calls `POST /api/outreach/claim` to change one row to `SENDING` **before** calling the AI model or Gmail. If a later node fails, investigate the `SENDING` row manually instead of sending it again blindly.
3. Ask the AI model for a concise email using only the stored project facts and your company's approved offering. Review the prompt and model output before activating automatic sending.
4. Sends via the connected Gmail node to the exact approved email, then calls `POST /api/outreach/sent` to set `Email Status = SENT`, `Email Sent UTC` and Gmail message ID. If Gmail succeeded but logging failed, check Sent Mail and resolve the `SENDING` row manually.

Your deactivated `DASNY + NYCSCA + Universal AI Fallback v4` workflow should remain off as a **scraper**; a separate n8n workflow can handle scheduling and email delivery. The repository does not include sender credentials or automatically send email. The sheet's recipient and sender credentials must be connected in n8n before activating it.

## Local run

```bash
pip install -r requirements.txt
playwright install chromium
# Set GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON, and ADMIN_TOKEN
uvicorn app.main:app --reload
```

Scraping can fail when a source changes markup or blocks a Render IP. Inspect `/api/status` and Render logs, then update that source parser; do not treat an empty tab as a successful scrape.

## Total Bid Data setup

Set `TOTALBIDDATA_USERNAME` and `TOTALBIDDATA_PASSWORD` privately in GitHub Actions secrets. Never commit a password or session token. The collector only reads source pages and writes the existing `Projects` tab; it never clicks Update Contract, Publish, Add or Delete on Total Bid Data. No sheet columns or dashboard layout change.

Each run discovers up to `TOTALBIDDATA_MAX_PAGES` per queue (default 1000) and reads details for up to `TOTALBIDDATA_DETAIL_LIMIT` projects (default 100). Missing projects are processed first, followed by the oldest stored projects; repeated runs progressively backfill the source. `/api/status` reports queue totals, the detail batch limit, errors and pending new records. Initial full backfill can require multiple runs. Increase the detail limit cautiously on free Render; n8n's fixed seven-minute status check may report that a larger run is still running.

Explicit Award, General Contractor, and Contractor contacts map to contractor fields. Bid Result contacts are retained in Description and are not assumed to be awarded contractors. Architect and Engineer roles map to Architect/Engineer. Contact and Owner roles map to source-contact fields. Solicitation, contract and PIN numbers are preserved in Description. Cancellation notices override the queue's stage. Contact evidence links require a Total Bid Data login. New records are not automatically approved for outreach.


### MyVendorLink setup

Set `MYVENDORLINK_EMAIL` and `MYVENDORLINK_PASSWORD` privately in GitHub Actions secrets, then redeploy. Optional `MYVENDORLINK_MAX_PAGES=100` and `MYVENDORLINK_DETAIL_LIMIT=100` control coverage. Missing credentials report `not_configured`. Each run reads active listings and verified detail pages into the existing Projects columns; no schema or dashboard changes are required. Missing records are processed first, followed by the oldest records. Check `myvendorlink` in GitHub Actions logs for coverage and errors.

The source uses agency plus solicitation number for identity. Detail URLs select a bid through the login session, so stored links point to the active list: sign in and find the agency and solicitation number recorded in the description. Primary procurement contacts populate source-contact columns. Planholders and bidders are not treated as awarded general contractors. Email approval and sending remain in the existing n8n flow.


### Free Render: external collection

Render now defaults to dashboard mode. Startup scraping and POST /run are disabled unless ENABLE_LOCAL_SCRAPING=true is explicitly set. Old SCRAPE_INTERVAL_HOURS values alone cannot start collection. Keep Render ENABLE_LOCAL_SCRAPING=false. Existing sheet columns, dashboard layout, and n8n outreach endpoints stay unchanged; disable the old n8n POST /run trigger while keeping reviewed outreach active.

The Collect bids GitHub Actions workflow runs at minutes 17 and 47 each hour (schedules can be delayed), and supports Actions → Collect bids → Run workflow. One run writes to the sheet at a time. Jobs can last up to 90 minutes; overlapping requests wait rather than run concurrently. Each run processes up to 100 detail records per private source, backfilling missing records before refreshing old ones. Total Bid Data scans up to 100 pages per queue and reports partial coverage when capped; raise that limit in the workflow only if its coverage logs require it. Secrets are checked before collection starts. Failed collectors or detail errors mark the job failed; partial backfill is reported in logs.

Add these repository secrets under Settings → Secrets and variables → Actions → New repository secret:

- GOOGLE_SHEETS_ID — the same sheet ID currently configured on Render.
- GOOGLE_SERVICE_ACCOUNT_JSON — the same full service-account JSON currently configured on Render; the sheet must already be shared with this service account.
- TOTALBIDDATA_USERNAME and TOTALBIDDATA_PASSWORD.
- MYVENDORLINK_EMAIL and MYVENDORLINK_PASSWORD.

Render environment variables do not transfer to GitHub Actions. Keep Google's existing sheet credentials on Render so it can read the ledger. No website passwords are committed. On Render /api/status, collection_mode=github_actions and status=external indicate dashboard mode; external run status is in GitHub Actions, not that process-local endpoint. Client records refresh from the sheet every minute. To run collection on a developer machine, provide the same environment variables and use python -m app.collect.
