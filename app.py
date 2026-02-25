from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Keshavpsn!8")
def _resolve_database_path() -> str:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return configured

    # Vercel serverless runtime only guarantees writable /tmp.
    if os.getenv("VERCEL"):
        return "/tmp/carpool.db"

    return "carpool.db"


DATABASE_PATH = _resolve_database_path()

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


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_: Any) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    if DATABASE_PATH not in (":memory:",):
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carpools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_initial TEXT NOT NULL,
            phone TEXT NOT NULL,
            flight_code TEXT NOT NULL,
            airport_code TEXT NOT NULL,
            airport_name TEXT NOT NULL,
            airport_location TEXT NOT NULL,
            flight_time_utc TEXT NOT NULL,
            flight_date_utc TEXT NOT NULL,
            seats_available INTEGER NOT NULL,
            notes TEXT NOT NULL,
            fetched_from TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def _cleanup_expired_entries() -> None:
    now_iso = _now_utc().isoformat()
    db = get_db()
    db.execute("DELETE FROM carpools WHERE expires_at <= ?", (now_iso,))
    db.commit()


def _fetch_flight_live(flight_code: str) -> dict[str, str]:
    try:
        with urlopen("https://opensky-network.org/api/states/all", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return {"status": "unknown", "source": "opensky_unavailable"}

    for state in payload.get("states") or []:
        callsign = (state[1] or "").strip().upper()
        if callsign == flight_code:
            observed = datetime.fromtimestamp(payload.get("time", 0), tz=timezone.utc) if payload.get("time") else _now_utc()
            expires_at = observed + timedelta(hours=6)
            return {
                "status": "airborne" if not state[8] else "on_ground",
                "source": "opensky",
                "flight_time_utc": observed.strftime("%H:%M"),
                "flight_date_utc": observed.strftime("%Y-%m-%d"),
                "expires_at": expires_at.isoformat(),
            }

    return {"status": "not_found_live", "source": "opensky"}


def _resolve_airport(code: str) -> tuple[str, str]:
    airport = AIRPORT_CODE_MAP.get(code)
    if airport:
        return airport["name"], airport["location"]
    return f"Airport {code}", "Unknown location"


def _serialize_entry(row: sqlite3.Row, include_phone: bool = False) -> dict[str, Any]:
    data = dict(row)
    if not include_phone:
        data.pop("phone", None)
    return data


def _require_admin() -> bool:
    return bool(session.get("admin_authed"))


@app.get("/")
def landing() -> Any:
    return render_template("welcome.html")


@app.get("/landing")
def landing_legacy() -> Any:
    return redirect(url_for("landing"), code=302)


@app.get("/add-flight-details")
def add_flight_details_page() -> Any:
    return render_template("add_flight_details.html")


@app.get("/find-a-carpool")
def find_a_carpool_page() -> Any:
    return render_template("find_a_carpool.html")


@app.get("/join")
def join_page() -> Any:
    return redirect(url_for("add_flight_details_page"), code=302)


@app.get("/search")
def search_page() -> Any:
    return redirect(url_for("find_a_carpool_page"), code=302)


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

    airport_code = data["airport_code"].upper().strip()
    if len(airport_code) != 3:
        return jsonify({"error": "airport_code must be a 3-letter airport code"}), 400

    flight_code = _clean_flight_code(data["flight_code"])
    flight_info = _fetch_flight_live(flight_code)
    airport_name, airport_location = _resolve_airport(airport_code)

    created_at = _now_utc().isoformat()
    expires_at = flight_info.get("expires_at") or (_now_utc() + timedelta(hours=12)).isoformat()

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO carpools (
            first_name,last_initial,phone,flight_code,airport_code,airport_name,airport_location,
            flight_time_utc,flight_date_utc,seats_available,notes,fetched_from,status,expires_at,created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["first_name"].strip().title(),
            data["last_initial"].strip()[:1].upper(),
            data["phone"].strip(),
            flight_code,
            airport_code,
            airport_name,
            airport_location,
            flight_info.get("flight_time_utc", "Unknown"),
            flight_info.get("flight_date_utc", "Unknown"),
            int(data.get("seats_available", 3) or 3),
            str(data.get("notes", "")).strip(),
            flight_info.get("source", "fallback"),
            flight_info.get("status", "unknown"),
            expires_at,
            created_at,
        ),
    )
    db.commit()

    row = db.execute("SELECT * FROM carpools WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"message": "Added to carpool database", "entry": _serialize_entry(row)}), 201


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    _cleanup_expired_entries()
    flight_code = _clean_flight_code(request.args.get("flight_code", ""))
    airport_code = request.args.get("airport_code", "").upper().strip()

    db = get_db()
    rows = db.execute("SELECT * FROM carpools ORDER BY created_at DESC").fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        score = 0
        reasons: list[str] = []
        if flight_code and entry["flight_code"] == flight_code:
            score += 70
            reasons.append("Exact flight code match")
        if airport_code and entry["airport_code"] == airport_code:
            score += 30
            reasons.append("Same airport code")
        if score > 0 or (not flight_code and not airport_code):
            public_row = _serialize_entry(row)
            public_row["match_score"] = score
            public_row["match_reasons"] = reasons
            results.append(public_row)

    results.sort(key=lambda r: r["match_score"], reverse=True)
    return jsonify({"count": len(results), "results": results})


@app.get("/api/carpools/<int:entry_id>")
def carpool_details(entry_id: int) -> Any:
    _cleanup_expired_entries()
    row = get_db().execute("SELECT * FROM carpools WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"entry": _serialize_entry(row, include_phone=True)})


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
    rows = get_db().execute("SELECT * FROM carpools ORDER BY created_at DESC").fetchall()
    return render_template("admin.html", entries=[dict(row) for row in rows])


@app.post("/admin/delete-all")
def admin_delete_all() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    db.execute("DELETE FROM carpools")
    db.commit()
    return redirect(url_for("admin_panel"))


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    db.execute("DELETE FROM carpools WHERE id = ?", (entry_id,))
    db.commit()
    return redirect(url_for("admin_panel"))


@app.post("/admin/edit/<int:entry_id>")
def admin_edit_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401

    first_name = request.form.get("first_name", "").strip().title()
    last_initial = request.form.get("last_initial", "").strip().upper()[:1]
    phone = request.form.get("phone", "").strip()
    notes = request.form.get("notes", "").strip()

    db = get_db()
    db.execute(
        """
        UPDATE carpools
        SET first_name = COALESCE(NULLIF(?, ''), first_name),
            last_initial = COALESCE(NULLIF(?, ''), last_initial),
            phone = COALESCE(NULLIF(?, ''), phone),
            notes = COALESCE(NULLIF(?, ''), notes)
        WHERE id = ?
        """,
        (first_name, last_initial, phone, notes, entry_id),
    )
    db.commit()
    return redirect(url_for("admin_panel"))


try:
    init_db()
except sqlite3.Error as exc:
    # Keep import alive so Vercel can return logs instead of immediate crash loop.
    print(f"[startup] database initialization failed: {exc}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
