from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import itertools
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Keshavpsn!8")


@dataclass
class CarpoolEntry:
    id: int
    first_name: str
    last_initial: str
    phone: str
    flight_code: str
    airport_code: str
    airport_name: str
    airport_location: str
    flight_time_utc: str
    flight_date_utc: str
    seats_available: int
    notes: str
    fetched_from: str
    status: str
    expires_at: str
    created_at: str


ENTRY_COUNTER = itertools.count(1)
ENTRIES: list[CarpoolEntry] = []


AIRPORT_CODE_MAP = {
    "SFO": {"name": "San Francisco International Airport", "location": "San Francisco, CA"},
    "ONT": {"name": "Ontario International Airport", "location": "Ontario, CA"},
    "LAX": {"name": "Los Angeles International Airport", "location": "Los Angeles, CA"},
    "JFK": {"name": "John F. Kennedy International Airport", "location": "New York, NY"},
    "EWR": {"name": "Newark Liberty International Airport", "location": "Newark, NJ"},
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_flight_code(code: str) -> str:
    return "".join(code.upper().strip().split())


def _cleanup_expired_entries() -> None:
    now = _now_utc()
    ENTRIES[:] = [e for e in ENTRIES if datetime.fromisoformat(e.expires_at) > now]


def _fetch_flight_live(flight_code: str) -> dict[str, Any]:
    """Best-effort live flight fetch via OpenSky states feed by callsign."""
    try:
        with urlopen("https://opensky-network.org/api/states/all", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return {"status": "unknown", "source": "opensky_unavailable"}

    states = payload.get("states") or []
    for state in states:
        callsign = (state[1] or "").strip().upper()
        if callsign == flight_code:
            observed = datetime.fromtimestamp(payload.get("time", 0), tz=timezone.utc) if payload.get("time") else _now_utc()
            # Heuristic: assume flight lands within 6 hours from observation.
            expires_at = observed + timedelta(hours=6)
            return {
                "status": "airborne" if not state[8] else "on_ground",
                "source": "opensky",
                "flight_time_utc": observed.strftime("%H:%M"),
                "flight_date_utc": observed.strftime("%Y-%m-%d"),
                "expires_at": expires_at.isoformat(),
                "origin_country": state[2],
            }

    return {"status": "not_found_live", "source": "opensky"}


def _resolve_airport(code: str) -> tuple[str, str]:
    airport = AIRPORT_CODE_MAP.get(code)
    if airport:
        return airport["name"], airport["location"]
    return f"Airport {code}", "Unknown location"


def _build_entry(data: dict[str, str]) -> CarpoolEntry:
    flight_code = _clean_flight_code(data.get("flight_code", ""))
    airport_code = data.get("airport_code", "").upper().strip()

    flight_info = _fetch_flight_live(flight_code)
    airport_name, airport_location = _resolve_airport(airport_code)

    created_at = _now_utc()
    expires_at = flight_info.get("expires_at") or (created_at + timedelta(hours=12)).isoformat()

    return CarpoolEntry(
        id=next(ENTRY_COUNTER),
        first_name=data["first_name"].strip().title(),
        last_initial=data["last_initial"].strip()[:1].upper(),
        phone=data["phone"].strip(),
        flight_code=flight_code,
        airport_code=airport_code,
        airport_name=airport_name,
        airport_location=airport_location,
        flight_time_utc=flight_info.get("flight_time_utc", "Unknown"),
        flight_date_utc=flight_info.get("flight_date_utc", "Unknown"),
        seats_available=int(data.get("seats_available", 3) or 3),
        notes=str(data.get("notes", "")).strip(),
        fetched_from=flight_info.get("source", "fallback"),
        status=flight_info.get("status", "unknown"),
        expires_at=expires_at,
        created_at=created_at.isoformat(),
    )


def _require_admin() -> bool:
    return bool(session.get("admin_authed"))


@app.get("/")
def landing() -> Any:
    return render_template("landing.html")


@app.get("/join")
def join_page() -> Any:
    return render_template("join.html")


@app.get("/search")
def search_page() -> Any:
    return render_template("search.html")


@app.post("/api/carpools")
def create_carpool() -> Any:
    _cleanup_expired_entries()
    data = request.get_json(silent=True) or request.form.to_dict()

    required = ["first_name", "last_initial", "phone", "flight_code", "airport_code"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    if len(data["last_initial"].strip()) != 1:
        return jsonify({"error": "last_initial must be exactly 1 character"}), 400

    if len(data["airport_code"].strip()) != 3:
        return jsonify({"error": "airport_code must be a 3-letter airport code"}), 400

    entry = _build_entry(data)
    ENTRIES.append(entry)
    payload = asdict(entry)
    payload.pop("phone")
    return jsonify({"message": "Added to carpool database", "entry": payload}), 201


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    _cleanup_expired_entries()
    flight_code = _clean_flight_code(request.args.get("flight_code", ""))
    airport_code = request.args.get("airport_code", "").upper().strip()

    results = []
    for entry in ENTRIES:
        score = 0
        reasons = []
        if flight_code and entry.flight_code == flight_code:
            score += 70
            reasons.append("Exact flight code match")
        if airport_code and entry.airport_code == airport_code:
            score += 30
            reasons.append("Same airport code")
        if score > 0 or (not flight_code and not airport_code):
            row = asdict(entry)
            row.pop("phone")
            row["match_score"] = score
            row["match_reasons"] = reasons
            results.append(row)

    results.sort(key=lambda r: r["match_score"], reverse=True)
    return jsonify({"count": len(results), "results": results})


@app.get("/api/carpools/<int:entry_id>")
def carpool_details(entry_id: int) -> Any:
    _cleanup_expired_entries()
    entry = next((e for e in ENTRIES if e.id == entry_id), None)
    if not entry:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"entry": asdict(entry)})


@app.post("/admin/login")
def admin_login() -> Any:
    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session["admin_authed"] = True
        return redirect(url_for("admin_panel"))
    return redirect(url_for("landing"))


@app.get("/admin")
def admin_panel() -> Any:
    if not _require_admin():
        return redirect(url_for("landing"))
    _cleanup_expired_entries()
    return render_template("admin.html", entries=[asdict(e) for e in ENTRIES])


@app.post("/admin/delete-all")
def admin_delete_all() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    ENTRIES.clear()
    return redirect(url_for("admin_panel"))


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    ENTRIES[:] = [e for e in ENTRIES if e.id != entry_id]
    return redirect(url_for("admin_panel"))


@app.post("/admin/edit/<int:entry_id>")
def admin_edit_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401

    entry = next((e for e in ENTRIES if e.id == entry_id), None)
    if not entry:
        return jsonify({"error": "Not found"}), 404

    entry.first_name = request.form.get("first_name", entry.first_name).strip().title()
    li = request.form.get("last_initial", entry.last_initial).strip().upper()
    entry.last_initial = li[:1] if li else entry.last_initial
    entry.phone = request.form.get("phone", entry.phone).strip()
    entry.notes = request.form.get("notes", entry.notes).strip()
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
