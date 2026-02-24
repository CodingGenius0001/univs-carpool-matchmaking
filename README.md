# University Carpool Matchmaking App

A web app that lets students join/search airport carpools using only a **flight code** (like `UA 2363`) and **3-letter airport code** (like `SFO`, `ONT`).

## ✅ Database persistence added
This app now stores carpool entries in a **real SQLite database file** (`carpool.db`) instead of in-memory Python lists.
That means:
- Data persists across app restarts on the same host.
- Multiple visitors share the same database-backed data.
- Search/admin views read from the DB, not local process memory.

Set a custom DB file path with:
- `DATABASE_PATH=/path/to/your.db`

## What changed
- Users are no longer asked for full flight details.
- The app fetches flight timing/status info automatically from live internet data (OpenSky best-effort).
- UX is separated into pages:
  - Landing page (`/`) with project intro and two action buttons.
  - Join page (`/join`) to add yourself to the database.
  - Search page (`/search`) to find matching carpools.
- Search results include **View More Info** to reveal contact details.
- Admin panel supports login, view database, edit entries, delete entry, and delete all.


## Navigation / Page structure
- `/` Welcome page with full-screen intro, tagline, and a “Scroll to Start” prompt.
- `/add-flight-details` Separate page for adding your info to the database.
- `/find-a-carpool` Separate page for searching available carpools.
- A shared top header bar appears across pages with links to Home, Find a Carpool, and Add Flight Details.

## Privacy behavior
- UI includes disclaimer: data is not sold and entries are auto-removed after flight window.
- Entries are automatically purged after expiration (derived from live status/heuristic).

## Admin login
- Username: `admin`
- Password: `Keshavpsn!8`
- Password verification uses hashed checking on the server.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open: http://localhost:8000

## Deploy on Vercel
This repo is configured with:
- `api/index.py` serverless entrypoint
- `vercel.json` catch-all route to Flask app

⚠️ Important: Vercel's filesystem is ephemeral, so SQLite files do **not** provide durable long-term persistence there.
For true durable production persistence on Vercel, use a managed DB service.

## If you want cloud-persistent DB next (recommended)
I can switch this to Postgres/Supabase. I would need:
1. Your DB provider choice (Supabase/Neon/Railway Postgres/etc.)
2. A `DATABASE_URL` connection string
3. Whether you want me to run a migration in this repo or keep auto-table creation behavior
