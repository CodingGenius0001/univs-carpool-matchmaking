# University Carpool Matchmaking App

A Flask web app for students to coordinate rides to the airport by matching on **flight code** (example: `UA 2363`) and **3-letter airport code** (example: `SFO`, `ONT`).

## Current app status (latest)
This repository currently serves a **welcome-first flow** with separate pages for:
- Adding your flight details to the database
- Finding available carpools

Data is persisted in SQLite (not in-memory), and admin controls are available for editing/deleting records.

---

## Active routes and what they render

### Web pages
- `GET /` → `templates/welcome.html` (main landing page)
- `GET /add-flight-details` → `templates/add_flight_details.html`
- `GET /find-a-carpool` → `templates/find_a_carpool.html`
- `GET /start-now` → `templates/start_now.html` (selection page reached only from landing CTA)
- `GET /admin` → `templates/admin.html` (admin-only)

### Compatibility redirects (legacy links)
- `GET /landing` → redirects to `/`
- `GET /join` → redirects to `/add-flight-details`
- `GET /search` → redirects to `/find-a-carpool`

### API endpoints
- `POST /api/carpools` → create a carpool record (requires departure date (MM-DD-YYYY), validates flight code + phone format)
- `GET /api/flights/suggest?query=UA` → best-effort live flight suggestions for dropdown UX
- `GET /api/carpools/search` → search/rank records (supports `departure_date` (`MM-DD-YYYY`))
- `GET /api/carpools/<id>` → fetch full details for one record (including phone)

### Admin actions
- `POST /admin/login`
- `POST /admin/edit/<id>`
- `POST /admin/delete/<id>`
- `POST /admin/delete-all`

---

## Database and persistence

### Vercel crash fix (FUNCTION_INVOCATION_FAILED)
The app now auto-selects a Vercel-safe default DB path:
- On Vercel: `/tmp/carpool.db`
- Elsewhere: `carpool.db`

This avoids startup crashes caused by trying to write SQLite files to read-only serverless paths.
You can still override with `DATABASE_PATH`.

- Default DB file: `carpool.db`
- Configurable path: `DATABASE_PATH=/path/to/file.db`
- Table is auto-created on startup (`init_db()` in `app.py`).
- Expired flights are auto-purged using `expires_at`.
- User-entered date is persisted as `requested_flight_date` for matching.

> Note: SQLite persists on a single host filesystem. It is **not durable** on ephemeral serverless filesystems.

---

## Current frontend file map

### Templates currently used by active routes
- `templates/welcome.html`
- `templates/add_flight_details.html`
- `templates/find_a_carpool.html`
- `templates/start_now.html`
- `templates/admin.html`

### Static files currently used
- `static/styles.css`
- `static/join.js`
- `static/search.js`

### Legacy templates still present in repo
These files exist but are not currently directly rendered by the active route mapping:
- `templates/index.html`
- `templates/landing.html`
- `templates/join.html`
- `templates/search.html`

---

## Admin login
- Username: `admin`
- Password: `Keshavpsn!8`
- Password verification is hash-based on the server via Werkzeug.

---

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Then open: `http://localhost:8000`

---

## Deploy on Vercel
This repo includes:
- `api/index.py` (Vercel Python entrypoint)
- `vercel.json` (catch-all routing to Flask app)

### Important Vercel note
Vercel filesystem is ephemeral. SQLite files are not long-term durable there.
For production persistence, use a managed DB (e.g., Supabase/Neon/Postgres) and migrate from SQLite.


## MySQL support (for real persistence across instances)
Set `DB_ENGINE=mysql` and provide:
- `MYSQL_HOST`
- `MYSQL_PORT` (default `3306`)
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

The app will auto-create the `carpools` table.

## Flight data providers
The app now uses **SerpApi Google Flights API** for present/future flight lookup and suggestions.

Set these env vars:
- `SERPAPI_API_KEY` (or `SERPAPI_KEY`)
- `SERPAPI_ENDPOINT` (optional, defaults to `https://serpapi.com/search.json`)

Notes:
- Old OpenSky / ADS-B / RapidAPI lookup code has been removed.
- Suggestions and validation now rely on SerpApi Google Flights responses.

If you want me to finish production wiring right now, send:
1. Vercel project name/environment where I should apply variables
2. Confirmation that these are set: `DB_ENGINE`, `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `SERPAPI_API_KEY`
