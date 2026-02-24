from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
import itertools

import json
from urllib.error import URLError, HTTPError
from urllib.request import urlopen
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


@dataclass
class CarpoolEntry:
    id: int
    first_name: str
    last_initial: str
    flight_number: str
    flight_time: str
    flight_date: str
    airport_name: str
    airport_location: str
    seats_available: int
    notes: str
    created_at: str


ENTRY_COUNTER = itertools.count(1)
ENTRIES: list[CarpoolEntry] = []


def _seed_demo_entries() -> None:
    if ENTRIES:
        return

    seed_time = datetime.utcnow() + timedelta(hours=2)
    ENTRIES.append(
        CarpoolEntry(
            id=next(ENTRY_COUNTER),
            first_name="Mia",
            last_initial="K",
            flight_number="AA274",
            flight_time=seed_time.strftime("%H:%M"),
            flight_date=seed_time.strftime("%Y-%m-%d"),
            airport_name="John F. Kennedy International Airport",
            airport_location="New York, NY",
            seats_available=2,
            notes="Can share ride from campus north gate.",
            created_at=datetime.utcnow().isoformat() + "Z",
        )
    )


def _normalize(value: str) -> str:
    return " ".join(value.strip().split()).lower()


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _score_match(entry: CarpoolEntry, filters: dict[str, str]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0

    entry_airport = _normalize(entry.airport_name)
    filter_airport = _normalize(filters.get("airport_name", ""))
    if filter_airport and filter_airport in entry_airport:
        score += 40
        reasons.append("Same airport name")

    entry_location = _normalize(entry.airport_location)
    filter_location = _normalize(filters.get("airport_location", ""))
    if filter_location and filter_location in entry_location:
        score += 20
        reasons.append("Similar airport location")

    entry_flight = _normalize(entry.flight_number)
    filter_flight = _normalize(filters.get("flight_number", ""))
    if filter_flight and filter_flight == entry_flight:
        score += 40
        reasons.append("Exact flight match")

    requested_dt = _parse_datetime(filters.get("flight_date", ""), filters.get("flight_time", ""))
    entry_dt = _parse_datetime(entry.flight_date, entry.flight_time)

    if requested_dt and entry_dt:
        delta_minutes = abs((entry_dt - requested_dt).total_seconds()) / 60
        time_window = int(filters.get("time_window_minutes", 120) or 120)
        if delta_minutes <= time_window:
            closeness = max(0, 1 - (delta_minutes / max(time_window, 1)))
            score += int(30 * closeness)
            reasons.append(f"Flight time within {int(delta_minutes)} minutes")

    return score, reasons


@app.get("/")
def home() -> Any:
    return render_template("index.html")


@app.post("/api/carpools")
def create_carpool() -> Any:
    data = request.get_json(silent=True) or request.form.to_dict()
    required_fields = [
        "first_name",
        "last_initial",
        "flight_number",
        "flight_time",
        "flight_date",
        "airport_name",
        "airport_location",
    ]
    missing = [field for field in required_fields if not str(data.get(field, "")).strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    entry = CarpoolEntry(
        id=next(ENTRY_COUNTER),
        first_name=data["first_name"].strip().title(),
        last_initial=data["last_initial"].strip()[:1].upper(),
        flight_number=data["flight_number"].strip().upper(),
        flight_time=data["flight_time"].strip(),
        flight_date=data["flight_date"].strip(),
        airport_name=data["airport_name"].strip(),
        airport_location=data["airport_location"].strip(),
        seats_available=int(data.get("seats_available", 3) or 3),
        notes=str(data.get("notes", "")).strip(),
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    ENTRIES.append(entry)
    return jsonify({"message": "Carpool entry created", "entry": asdict(entry)}), 201


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    filters = request.args.to_dict()
    include_all = request.args.get("include_all", "false").lower() == "true"

    matches = []
    for entry in ENTRIES:
        score, reasons = _score_match(entry, filters)
        if include_all or score > 0:
            payload = asdict(entry)
            payload["match_score"] = score
            payload["match_reasons"] = reasons
            matches.append(payload)

    matches.sort(key=lambda row: row["match_score"], reverse=True)
    return jsonify(
        {
            "count": len(matches),
            "filters": filters,
            "results": matches,
        }
    )


@app.get("/api/flight-status")
def flight_status() -> Any:
    """Best-effort OpenSky lookup by callsign (e.g., 'UAL123')."""
    flight_number = request.args.get("flight_number", "").strip().upper()
    if not flight_number:
        return jsonify({"error": "flight_number query parameter is required"}), 400

    try:
        with urlopen("https://opensky-network.org/api/states/all", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return (
            jsonify(
                {
                    "flight_number": flight_number,
                    "status": "unknown",
                    "message": "OpenSky unavailable right now. Consider adding a paid flight-status API for reliability.",
                }
            ),
            503,
        )

    states = payload.get("states") or []
    for state in states:
        callsign = (state[1] or "").strip().upper()
        if callsign == flight_number:
            updated_at = datetime.utcfromtimestamp(payload.get("time", 0)) if payload.get("time") else None
            return jsonify(
                {
                    "flight_number": flight_number,
                    "status": "airborne",
                    "source": "OpenSky",
                    "last_seen_utc": updated_at.isoformat() + "Z" if updated_at else None,
                    "position": {"longitude": state[5], "latitude": state[6], "altitude_m": state[7]},
                    "velocity_mps": state[9],
                    "origin_country": state[2],
                }
            )

    return jsonify(
        {
            "flight_number": flight_number,
            "status": "not_found_live",
            "message": "No live OpenSky match for this callsign right now.",
        }
    )


@app.get("/api/llm-guide")
def llm_guide() -> Any:
    return jsonify(
        {
            "description": "LLM-friendly endpoints for University Carpool Matchmaking App",
            "endpoints": [
                {
                    "method": "POST",
                    "path": "/api/carpools",
                    "purpose": "Create a rider/carpool listing",
                    "required_fields": [
                        "first_name",
                        "last_initial",
                        "flight_number",
                        "flight_time",
                        "flight_date",
                        "airport_name",
                        "airport_location",
                    ],
                },
                {
                    "method": "GET",
                    "path": "/api/carpools/search",
                    "purpose": "Find matches by flight metadata",
                    "recommended_query": [
                        "flight_number",
                        "flight_time",
                        "flight_date",
                        "airport_name",
                        "airport_location",
                        "time_window_minutes",
                    ],
                },
                {
                    "method": "GET",
                    "path": "/api/flight-status",
                    "purpose": "Live status lookup via OpenSky callsign match",
                    "required_query": ["flight_number"],
                },
            ],
        }
    )


_seed_demo_entries()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
