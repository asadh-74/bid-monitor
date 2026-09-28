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
| Construction.com / Dodge | Requires an authorized Dodge project API or licensed data feed; the marketing homepage has no downloadable project listing. |

Each collector fails independently and `/api/status` reports its most recent in-process results. `/health` only checks that FastAPI is up. The current site must be redeployed from this branch before new sources appear; repository code alone does not change the live service.

## Render configuration

Keep the existing `GOOGLE_SHEETS_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`. Set a long random `ADMIN_TOKEN` as a Render environment secret. The service account must have Editor access to the sheet. The app creates `Projects` with these columns: Source, Source ID, Source URL, Title, Stage, Deadline, Contractor, Contractor Email, Contact Evidence, Description, First Seen UTC, Last Seen UTC, Email Status, Email Sent UTC, Review Notes. Existing `Bids` and SCA tabs are not modified or deleted.

A free Render service may sleep between visits; the app starts a collection on wake and repeats it every `SCRAPE_INTERVAL_HOURS` while running. For reliable periodic collection even while it sleeps, schedule an n8n HTTP Request `POST https://bid-monitor.onrender.com/run` with `X-Admin-Token` in n8n's Header Auth credential. The endpoint returns `started` before a run completes; poll `/api/status` for per-source results. Avoid triggering overlapping runs.

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
