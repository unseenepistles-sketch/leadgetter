# Lead Generation System

A compliant **niche → leads → email** engine for creators and small businesses.
You type search terms (and optionally a location); it finds the top **public
creators** in that niche for research and partnership outreach, captures leads
who **opt in** through a lead-magnet landing page, stores them in a database
(mirrored to a Google Sheet), and runs email campaigns to the people who
consented — all from one dashboard, using free / low-cost tools.

## What it does (and deliberately doesn't)

**Does**
- 🔎 Discover top creators in a niche via the **YouTube Data API** (official) and
  public **Apify** actors for Instagram/TikTok — public profile data only.
- 🧲 Capture leads through an opt-in **landing page** (consent required, timestamped).
- 🗂 Store leads in Postgres/SQLite and mirror them to **Google Sheets**.
- 🤝 Track **partnership outreach** to creators, with AI-drafted messages you send yourself.
- ✉️ Send **email campaigns** to your opted-in list via self-hosted **Listmonk**
  (double opt-in + one-click unsubscribe built in).
- 🧠 Use **Claude** — Haiku 4.5 for cheap niche-term expansion, Sonnet 5 for copywriting.
- 📊 A **dashboard** with live metrics.

**Doesn't** — by design, because it would get your accounts banned and your
domain blacklisted, and it isn't legal:
- No scraping of followers' emails/phone numbers (that data isn't exposed anyway).
- No automated cold DMing of individuals.
- No emailing anyone who didn't opt in. Every lead has a recorded `consent_at`.

## Quick start (local, no keys needed to boot)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: fill in keys to enable integrations
uvicorn app.main:app --reload
```

Open http://localhost:8000 — the dashboard. The app runs on SQLite by default
and every integration degrades gracefully: unset keys simply disable that
feature. Fill in `.env` to switch things on:

| Feature | Env vars |
|---|---|
| AI (term expansion + copy) | `ANTHROPIC_API_KEY` |
| YouTube discovery | `YOUTUBE_API_KEY` |
| Instagram/TikTok discovery | `APIFY_TOKEN` |
| Email campaigns | `LISTMONK_URL`, `LISTMONK_USER`, `LISTMONK_PASSWORD`, `LISTMONK_LIST_ID` |
| Sheets sync | `GOOGLE_SA_JSON` (path to service-account JSON), `SHEET_ID` |
| Your offer | `BRAND_NAME`, `PRODUCT`, `LEAD_MAGNET` |

## Full stack (with Postgres + Listmonk)

```bash
cp .env.example .env            # set LISTMONK_USER / LISTMONK_PASSWORD at least
docker compose up -d --build
```

- App: http://localhost:8000
- Listmonk admin: http://localhost:9000 (log in with `LISTMONK_USER` / `LISTMONK_PASSWORD`),
  create a **double-opt-in list**, note its numeric id, set it as `LISTMONK_LIST_ID`,
  add an SMTP server (Amazon SES ~$0.10/1k, or a free tier like Brevo 300/day).

## MCP servers (agent-assisted operation)

`.mcp.json` configures two Model Context Protocol servers for operating the
system from an AI agent (and for testing scrapers/sheets by hand):

- **Apify** (`@apify/actors-mcp-server`) — run public scrapers. Needs `APIFY_TOKEN`.
- **Google Sheets** (`mcp-google-sheets`) — inspect/edit the leads sheet. Needs
  `GOOGLE_SA_JSON` + `SHEET_ID`.

The running app calls the underlying APIs directly; the MCPs are for interactive use.

## How to use it

1. **Discover** — on the dashboard, enter e.g. `christianity awakening spirituality`
   (or `golf` + location `Kenya`), pick platforms, run. Creators appear under **Creators**.
2. **Reach out** — on **Outreach**, draft a partnership message with AI and move
   creators through the pipeline as you contact them.
3. **Capture leads** — share your **Landing page** (`/l`). Opt-ins become leads
   (subscribed to Listmonk with double opt-in, mirrored to your Sheet).
4. **Email** — on **Campaigns**, AI-draft a campaign to your opted-in list and send.

## Project layout

```
app/
  main.py  config.py  db.py  models.py  jobs.py  web.py
  discovery/   youtube.py  apify.py  rank.py  service.py  routes.py
  ai/          claude.py
  capture/     routes.py            # landing + double-opt-in
  store/       sheets.py            # DB -> Google Sheet
  email/       listmonk.py  routes.py
  outreach/    routes.py
  dashboard/   routes.py
  templates/   *.html
docker-compose.yml  Dockerfile  .mcp.json  requirements.txt
```

## Costs

Listmonk self-host: free · SMTP: SES ~$0.10/1k or Brevo free 300/day · Apify:
free $5/mo credit · YouTube + Sheets APIs: free · Anthropic: pennies per run ·
Hosting: free locally, ~$5/mo VPS in production.
