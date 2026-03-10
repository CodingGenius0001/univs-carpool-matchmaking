# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Campus2Air — a Flask web app for university students to coordinate shared rides to the airport. Students enter flight details, and the system matches them with others on the same flight/airport/date.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (SQLite by default)
python app.py
# or: flask run

# The app runs on http://127.0.0.1:5000

# Generate a Werkzeug password hash for ADMIN_PASSWORD_HASH
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YourPassword'))"
```

There are no tests or linting configured in this project.

## Environment Variables

- `DB_ENGINE` — `sqlite` (default) or `mysql`
- `DATABASE_PATH` — SQLite file path (defaults to `carpool.db`, `/tmp/carpool.db` on Vercel)
- `FLASK_SECRET_KEY` — session secret
- `SERPAPI_API_KEY` / `SERPAPI_KEY` — for Google Flights lookup via SerpApi
- MySQL: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_PORT`, `MYSQL_SSL`
- **Admin** (one of these two is required — no hardcoded fallback):
  - `ADMIN_PASSWORD_HASH` — pre-computed Werkzeug hash (most secure)
  - `ADMIN_PASSWORD` — plaintext password (hashed at startup)
  - `ADMIN_USERNAME` — defaults to `"admin"`
- **Stripe** (subscription billing):
  - `STRIPE_SECRET_KEY` — Stripe secret API key
  - `STRIPE_PUBLISHABLE_KEY` — Stripe publishable key (used in frontend templates)
  - `STRIPE_WEBHOOK_SECRET` — Stripe webhook signing secret (from dashboard)
  - `STRIPE_PRICE_MONTHLY` — Stripe price ID for $0.99/month recurring plan
  - `STRIPE_PRICE_ANNUAL` — Stripe price ID for $8.28/year recurring plan
  - `STRIPE_PRICE_SEARCH_PACK` — Stripe price ID for $2.99 one-time search pack

## Architecture

**Single-file backend**: The entire backend lives in `app.py` (~2300 lines). It contains the Flask app, database layer, flight API integration, all route handlers, subscription logic, and admin panel logic.

**Deployment**: Vercel serverless via `api/index.py`, which simply imports `app` from `app.py`. All routes are proxied through `api/index.py` per `vercel.json`.

**Database**: `DBAdapter` class in `app.py` abstracts over MySQL (TiDB Cloud) and SQLite. It auto-falls back to SQLite if MySQL connection fails. Uses `%s` placeholders for MySQL, `?` for SQLite, with dynamic replacement via `db.placeholder`. Five tables:
- `carpools` — main ride entries (flight details, contact info, seats, expiration)
- `party_members` — users who joined a carpool (carpool_id + user_email)
- `users` — user profile cache (email, name, phone, created_at used for trial calculation)
- `notifications` — dismissible messages (e.g., disband notifications)
- `subscriptions` — Stripe billing state (customer_id, subscription_id, plan_type, sub_status, search_credits, current_period_end)

**Authentication**: Two separate auth systems:
1. **User auth** — Firebase-based (client-side), email stored in session via `/auth/firebase-callback`
2. **Admin auth** — Werkzeug password hash loaded from env var at startup (`ADMIN_PASSWORD_HASH` or `ADMIN_PASSWORD`), session-based with 30-min expiry. App raises `RuntimeError` at startup if neither env var is set.

**Subscription system**: Access control is gated by `get_user_access(user_email)` which checks in priority order:
1. 30-day free trial from `users.created_at` — if trial active AND no active paid sub, returns `tier: 'trial'`; if active paid sub exists during trial, falls through to return the actual paid tier
2. Active recurring subscription (`sub_status='active'` + `current_period_end` in future) — returns `tier: 'monthly'` or `tier: 'annual'`
3. Search credits pack — returns `tier: 'search_pack'`, `can_create: False`
4. Locked out — returns `tier: 'none'`, all `can_*` False

Stripe billing is webhook-driven (`/webhooks/stripe`). Key events handled: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.deleted`. Monthly→Annual upgrade uses `stripe.Subscription.modify()` in-place via `POST /api/subscription/upgrade`.

**Flight lookup**: SerpApi Google Flights integration searches by route (departure_id + arrival_id), not by flight number directly. `_lookup_present_or_future_flights()` and `_suggest_flights_for_airline()` handle flight code resolution using airline hub airports.

**Frontend**: Server-rendered Jinja2 templates in `templates/` with vanilla JS in `static/`. Key JS files:
- `auth.js` — Firebase auth flow
- `search.js` — carpool search, match scoring, pre-flight subscription check (redirects to `/pricing` on 403 or 0 credits)
- `join.js` — party join/leave/manage logic
- `subscription.js` — `C2ASub` global: badge, trial banner, credits indicator, paywall overlay, Stripe checkout redirect, billing portal
- `theme-global.js` — light/dark mode toggle

**Key patterns**:
- Always use `db.placeholder` (not a hardcoded `?` or `%s`) in SQL queries — it switches automatically based on engine
- Entries auto-expire at 23:59 UTC on departure day; cleanup runs on each request in `_ensure_db()`
- Flight codes validated against `^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$`; airline prefix mapped via `AIRLINE_CODES` dict
- Phone format enforced as `+1 (XXX) XXX XXXX`
- API endpoints under `/api/`, pages at root paths, admin under `/admin/`
- `ensure_columns()` in `DBAdapter` handles schema migrations for existing deployments (ALTER TABLE) — use this pattern when adding new columns rather than modifying `init_schema()` alone

**Admin panel** (`/admin`): Beyond carpool management, the admin panel has a Users & Subscriptions section with per-user actions: Edit (profile + subscription fields), Grant Monthly, Grant Annual, +3 Credits, Reset Trial. These bypass Stripe entirely and write directly to the DB. Key admin endpoints:
- `POST /admin/grant-subscription` — grant monthly/annual (sets period_end) or add 3 search credits
- `POST /admin/reset-user-trial` — sets `users.created_at` to now and clears subscription fields
- `POST /admin/edit-user` — edit any user profile or subscription field
