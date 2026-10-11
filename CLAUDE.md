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

# `python app.py` serves http://127.0.0.1:8000 (`flask run` uses :5000)
```

There are no tests or linting configured in this project.

## Environment Variables

- `DB_ENGINE` — `sqlite` (default) or `mysql`
- `DATABASE_PATH` — SQLite file path (defaults to `carpool.db`, `/tmp/carpool.db` on Vercel)
- `FLASK_SECRET_KEY` — session secret. Must be set in deployed environments; if missing, a random per-process key is used (sessions won't survive restarts or be shared across serverless instances)
- `FLASK_DEBUG` — enables the Werkzeug debugger for `python app.py` (default on; binds to 127.0.0.1 only)
- `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` (preferred) or `ADMIN_PASSWORD` — admin panel is disabled unless one of these is set
- `FIREBASE_PROJECT_ID` — Firebase project used to verify ID tokens
- `ALLOW_SQLITE_FALLBACK` — if MySQL is unreachable, fall back to SQLite (default: on locally, off on Vercel, where requests fail with 503 instead of writing to ephemeral `/tmp`)
- MySQL: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_PORT`, `MYSQL_SSL`

## Architecture

**Single-file backend**: The entire backend lives in `app.py` (~2000 lines). It contains the Flask app, database layer, all route handlers, and admin panel logic.

**Deployment**: Vercel serverless via `api/index.py`, which simply imports `app` from `app.py`. All routes are proxied through `api/index.py` per `vercel.json`.

**Database**: `DBAdapter` class in `app.py` abstracts over MySQL (TiDB Cloud) and SQLite. If MySQL is unreachable it either falls back to SQLite (`ALLOW_SQLITE_FALLBACK`, local default) or raises `DatabaseUnavailable`, which the error handler turns into a 503 (Vercel default); the next request retries MySQL. Uses `%s` placeholders for MySQL, `?` for SQLite, with dynamic replacement. Seven tables:
- `carpools` — main ride entries (flight details, contact info, seats, expiration, `share_token` for invite links, `direction`)
- `party_members` — users who joined a carpool (carpool_id + user_email)
- `users` — user profile cache (email, name, phone)
- `notifications` — dismissible messages (e.g., disband notifications)
- `join_log` — append-only join history for the rolling join limit (pruned after 48h)
- `carpool_history` — one row per carpool, kept after the carpool is deleted (flight, airport, date, seats, peak members, outcome: departed/disbanded/abandoned/admin_deleted; no personal data). Written by `_history_open` / `_history_bump_peak` / `_history_close`
- `activity_log` — admin-dashboard events (`signup`, `login`, `created`, `joined`, `left`, `removed`, `transferred`, `disbanded`, `shared`, `departed`, `admin_deleted`) via `_log_event()`; pruned after 180 days (`ACTIVITY_RETENTION`)

**Admin dashboard**: `/admin?days=7|30|90` renders `_admin_dashboard()` (KPIs, per-day series in Pacific time, open carpools with riders, students, past carpools, activity feed). Charts are dependency-free SVG drawn by `static/admin-dashboard.js` from the `dash-data` JSON block

**Authentication**: Two separate auth systems:
1. **User auth** — Firebase-based (client-side), email stored in session via `/auth/firebase-callback`
2. **Admin auth** — Werkzeug password hash from env vars, session-based with 30-min expiry

**Flight data**: There is no external flight lookup. Users enter their flight code; the airline name is auto-filled client-side from the `AIRLINE_CODES` prefix map via `/api/airlines/suggest`.

**Frontend**: Server-rendered Jinja2 templates in `templates/` with vanilla JS in `static/`. Key JS files:
- `auth.js` — Firebase auth flow
- `search.js` — carpool search with match scoring
- `join.js` — party join/leave/manage logic
- `theme-global.js` — light/dark mode toggle

**Key patterns**:
- Entries auto-expire at 23:59 UTC the day after the departure date; `_cleanup_expired_entries()` runs at the start of the create/search/details endpoints
- Join limit: a user can join at most `JOIN_LIMIT_COUNT` (2) carpools created by others per rolling 24h (`join_log`); leaving does not refund a join
- Trip direction: `carpools.direction` is `to_airport` (campus → airport, the default and what older rows are) or `from_airport` (airport → campus). For `from_airport`, `airport_code` is the arrival airport, the date is the landing date and `planned_departure_time` is when to meet at the airport. Search treats `direction` as a hard filter; UI wording switches via `data-to` / `data-from` attributes; `_trip_label()` gives "to LAX" / "from LAX" for messages
- Search: leave time is a hard filter (±`LEAVE_TIME_WINDOW_MINUTES`, 120); other fields add to the match score
- Flight codes validated against `^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$`; airport codes must be 3 letters; notes ≤500 chars, removal/disband reasons ≤300; departure date must be today (UTC, minus one day of slack) through one year out
- Phone format enforced as `+1 (XXX) XXX XXXX`
- Dates are shown and entered US-style (MM/DD/YYYY). Date fields use `<input type="date" data-us-date>` enhanced by `static/date-input.js` (a MM/DD/YYYY text box + calendar button; the hidden native input keeps the YYYY-MM-DD value), because native date inputs follow the browser's region format
- Invite links: `/c/<share_token>` shows a carpool summary and a join prompt. Tokens are random, created on first `GET /api/carpools/<id>/share-link` (members only) and never included in search/member API responses. Logged-out visitors are sent to login and returned to the invite via `session["post_login_next"]` (only `/c/...` paths are allowed)
- API endpoints under `/api/`, pages at root paths, admin under `/admin/`
