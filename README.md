# LeadSystem

Creator-led, permission-based lead generation — with an **open-source AI layer**
(no Anthropic key, no per-call bill).

You type a niche → it finds the top creators → you reach their audience the two
ways that actually work and are legal (creator partnerships + interest-based
ads) → interested people opt in on your page → you email the people who opted
in. Everything is stored in SQLite and (optionally) mirrored to Google Sheets.

---

## How the system runs

1. **Type a niche** — search terms + optional location (e.g. `christianity
   awakening spirituality`, or `golf` + `Kenya`).
2. **It finds the top creators** in that niche on a platform (Instagram /
   YouTube / TikTok), ranked by following — public profile data only.
3. **Reach their audience — two compliant routes:**
   - **Partner with the creators.** One click drafts a shoutout / affiliate
     outreach message (AI-written) you can send to each creator.
   - **Interest-based ads.** Export the discovered creators as ad-targeting
     seeds (CSV) plus auto-derived interest keywords, and paste them into Meta
     or TikTok Ads Manager to target those creators' audiences at scale.
4. **Interested people opt in** on your landing page — email captured with an
   explicit consent checkbox (consent + timestamp + IP are logged).
5. **Leads are stored** in the database and mirrored to Google Sheets.
6. **You email the opted-in leads** — AI-drafted campaigns, sent only to
   subscribed leads, every message with a working unsubscribe link.
7. **The dashboard shows it all** — creators found, leads captured, emails sent,
   and live status of every integration.

## A note on "scrape the followers"

This system deliberately does **not** scrape a creator's follower list to harvest
personal emails/phone numbers for cold outreach. Two reasons:

- **The data isn't there.** Instagram/TikTok follower lists expose usernames,
  not emails or phone numbers — there's nothing to harvest.
- **Doing it is illegal and a ban risk.** Harvesting personal contact info and
  cold-messaging it violates CAN-SPAM (email), the TCPA (calls/texts — up to
  $1,500 per message), and GDPR, and gets accounts banned.

The compliant routes above reach the *same* audience — a big creator's
followers — without any of that exposure. That's what steps 3a and 3b are for.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional — runs fine without editing
python run.py                 # http://localhost:8020
```

With **no** configuration it runs end-to-end on realistic sample creators, a
template-based AI fallback, and dry-run email — so you can click through the
whole workflow immediately. Add credentials to go live.

Run the tests:

```bash
pytest -q
```

The operator UI is a single-page **console** served at `/` (see `app/templates/
console.html`), backed by the JSON API in `app/routes_api.py`. The public opt-in
page your audience visits is at `/landing`.

---

## Deploy (get a live link)

The app runs on any host. `render.yaml` makes [Render](https://render.com)
turnkey:

1. In Render: **New + → Blueprint** → connect this repo/branch.
2. Render reads `render.yaml` and prompts for the `sync: false` values — your
   **`LLM_MODEL`**, **`LLM_API_KEY`** (OpenRouter), and **`APP_BASE_URL`** (the
   URL Render assigns, e.g. `https://leadsystem.onrender.com`).
3. **Apply** → you get a live HTTPS URL running the console.

Start command (any host): `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
Note: the free tier's disk is ephemeral (leads reset on redeploy) — add a
persistent disk or point `DATABASE_URL` at Postgres for durable storage.

---

## The AI layer (open-source, no Anthropic)

All drafting (creator outreach + email campaigns) goes through one small client,
`app/ai/llm.py`, which speaks the **OpenAI-compatible Chat Completions API**.
That one implementation covers every supported open-model provider — pick one in
`.env`:

| Provider          | Cost           | Setup                                                        |
|-------------------|----------------|-------------------------------------------------------------|
| **Ollama** (default) | Free, local    | Install Ollama, `ollama pull llama3.1:8b`. No API key.      |
| Groq              | Free tier      | Set `LLM_API_KEY` to a Groq key; serves Llama/Mixtral fast. |
| OpenRouter        | Free models    | Set `LLM_API_KEY`; use a `:free` model id.                  |
| openai_compatible | Your own       | Point `LLM_BASE_URL` at vLLM / LM Studio / any gateway.     |

If the model endpoint is unreachable, drafting falls back to solid built-in
templates, so the product never hard-fails on the AI.

---

## Configuration

Everything is optional; see `.env.example` for the full list.

| Area       | Keys                                            | Without it |
|------------|-------------------------------------------------|------------|
| AI         | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY` | Template drafts |
| Discovery  | `YOUTUBE_API_KEY` (free), `DISCOVERY_PROVIDER`, `APIFY_TOKEN` (IG/TikTok) | Sample creators |
| Sheets     | `SHEETS_ENABLED`, `SHEETS_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | DB only |
| Email      | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Dry-run |

---

## Project layout

```
app/
  main.py            FastAPI routes + dashboard
  config.py          Env-driven settings + capability flags
  database.py        SQLite / SQLAlchemy
  models.py          Creator, Lead, Campaign, EmailEvent
  state.py           Operator preferences (offer, name, niche)
  ai/llm.py          Open-source LLM client (Ollama / OpenAI-compatible)
  services/
    creators.py      Discovery (Apify + sample fallback)
    outreach.py      Creator partnership drafting
    leads.py         Consent-first opt-in capture
    campaigns.py     Draft + send to opted-in leads (unsubscribe built in)
    sheets.py        Google Sheets mirror
    ads.py           Ad-audience export (Meta/TikTok targeting seeds)
  templates/         Dashboard + opt-in landing page
tests/test_smoke.py  End-to-end smoke tests on sample data
```
