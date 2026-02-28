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
```

There are no tests or linting configured in this project.

## Environment Variables

- `DB_ENGINE` — `sqlite` (default) or `mysql`
- `DATABASE_PATH` — SQLite file path (defaults to `carpool.db`, `/tmp/carpool.db` on Vercel)
- `FLASK_SECRET_KEY` — session secret
- `SERPAPI_API_KEY` / `SERPAPI_KEY` — for Google Flights lookup via SerpApi
- MySQL: `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_PORT`, `MYSQL_SSL`

## Architecture

**Single-file backend**: The entire backend lives in `app.py` (~1674 lines). It contains the Flask app, database layer, flight API integration, all route handlers, and admin panel logic.

**Deployment**: Vercel serverless via `api/index.py`, which simply imports `app` from `app.py`. All routes are proxied through `api/index.py` per `vercel.json`.

**Database**: `DBAdapter` class in `app.py` abstracts over MySQL (TiDB Cloud) and SQLite. It auto-falls back to SQLite if MySQL connection fails. Uses `%s` placeholders for MySQL, `?` for SQLite, with dynamic replacement. Four tables:
- `carpools` — main ride entries (flight details, contact info, seats, expiration)
- `party_members` — users who joined a carpool (carpool_id + user_email)
- `users` — user profile cache (email, name, phone)
- `notifications` — dismissible messages (e.g., disband notifications)

**Authentication**: Two separate auth systems:
1. **User auth** — Firebase-based (client-side), email stored in session via `/auth/firebase-callback`
2. **Admin auth** — Werkzeug password hash, session-based with 30-min expiry, hardcoded credentials

**Flight lookup**: SerpApi Google Flights integration searches by route (departure_id + arrival_id), not by flight number directly. `_lookup_present_or_future_flights()` and `_suggest_flights_for_airline()` handle flight code resolution using airline hub airports.

**Frontend**: Server-rendered Jinja2 templates in `templates/` with vanilla JS in `static/`. Key JS files:
- `auth.js` — Firebase auth flow
- `search.js` — carpool search with match scoring
- `join.js` — party join/leave/manage logic
- `theme-global.js` — light/dark mode toggle

**Key patterns**:
- Entries auto-expire at 23:59 UTC on departure day; cleanup runs on each request in `_ensure_db()`
- Flight codes validated against `^[A-Z]{2,3}\d{1,4}[A-Z]?$`; airline prefix mapped via `AIRLINE_CODES` dict
- Phone format enforced as `+1 (XXX) XXX XXXX`
- API endpoints under `/api/`, pages at root paths, admin under `/admin/`
