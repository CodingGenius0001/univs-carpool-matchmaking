# University Carpool Matchmaking App

A web app that lets students join/search airport carpools using only a **flight code** (like `UA 2363`) and **3-letter airport code** (like `SFO`, `ONT`).

## What changed
- Users are no longer asked for full flight details.
- The app fetches flight timing/status info automatically from live internet data (OpenSky best-effort).
- UX is separated into pages:
  - Landing page (`/`) with project intro and two action buttons.
  - Join page (`/join`) to add yourself to the database.
  - Search page (`/search`) to find matching carpools.
- Search results include **View More Info** to reveal contact details.
- Admin panel supports login, view database, edit entries, delete entry, and delete all.

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

Deploy steps:
1. Push repository to GitHub.
2. Import into Vercel.
3. Keep preset as **Other**.
4. Deploy.
