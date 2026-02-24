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

## Hosting
You can deploy this on Render, Railway, Fly.io, or any VPS that supports Python/Flask.
