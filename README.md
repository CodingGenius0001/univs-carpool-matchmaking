# University Carpool Matchmaking App

A lightweight web app for students to share flight details and find nearby airport carpool matches.

## Features
- Submit a carpool listing with:
  - First name
  - Last initial
  - Flight number
  - Flight date + time
  - Airport name + location
  - Seats available + notes
- Search carpools with matching/scored filters.
- LLM-friendly JSON endpoints.
- Optional live flight lookup endpoint using OpenSky (`/api/flight-status`) based on callsign.

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Then open http://localhost:8000.

## API endpoints for LLM use
- `POST /api/carpools` create a listing.
- `GET /api/carpools/search` search and rank listings.
- `GET /api/flight-status?flight_number=UAL123` attempt live status lookup.
- `GET /api/llm-guide` machine-readable endpoint guide.

## Deploy on Vercel
This repository is preconfigured for Vercel Python serverless deployment via:
- `api/index.py` (entrypoint)
- `vercel.json` (routing/build config)

### Steps
1. Push this repo to GitHub.
2. In Vercel, click **Add New Project** and import the repository.
3. Keep framework preset as **Other**.
4. Deploy (no special build command needed).

### Notes for production
- The current app stores carpool entries in memory. On Vercel, serverless instances are ephemeral, so data may reset between cold starts.
- For persistent production data, connect a DB like Supabase/Postgres or Firebase.

## Other hosting options
You can also deploy this on Render, Railway, Fly.io, or any VPS that supports Python/Flask.
