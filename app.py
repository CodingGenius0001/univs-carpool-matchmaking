from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Keshavpsn!8")
FLIGHT_CODE_PATTERN = re.compile(r"^[A-Z]{2,3}\d{1,4}[A-Z]?$")
PHONE_PATTERN = re.compile(r"^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$")


def _resolve_database_path() -> str:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "/tmp/carpool.db"
    return "carpool.db"


DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()  # sqlite | mysql
DATABASE_PATH = _resolve_database_path()

AIRPORT_CODE_MAP = {
    "SFO": {"name": "San Francisco International Airport", "location": "San Francisco, CA"},
    "ONT": {"name": "Ontario International Airport", "location": "Ontario, CA"},
    "LAX": {"name": "Los Angeles International Airport", "location": "Los Angeles, CA"},
    "JFK": {"name": "John F. Kennedy International Airport", "location": "New York, NY"},
    "EWR": {"name": "Newark Liberty International Airport", "location": "Newark, NJ"},
}


class DBAdapter:
    def __init__(self) -> None:
        self.engine = DB_ENGINE
        self.placeholder = "%s" if self.engine == "mysql" else "?"

    def get_conn(self) -> Any:
        if "db" in g:
            return g.db

        if self.engine == "mysql":
            try:
                import pymysql
                from pymysql.cursors import DictCursor
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(f"PyMySQL is required for DB_ENGINE=mysql: {exc}")

            g.db = pymysql.connect(
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                user=os.getenv("MYSQL_USER", "root"),
                password=os.getenv("MYSQL_PASSWORD", ""),
                database=os.getenv("MYSQL_DATABASE", "carpool"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                autocommit=False,
                cursorclass=DictCursor,
            )
            return g.db

        # sqlite default
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
        return g.db

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        if self.engine == "sqlite":
            return [dict(r) for r in rows]
        return list(rows)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        conn = self.get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        last_id = getattr(cur, "lastrowid", 0)
        conn.commit()
        cur.close()
        return int(last_id or 0)

    def init_schema(self) -> None:
        if self.engine == "mysql":
            try:
                import pymysql
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(f"PyMySQL is required for DB_ENGINE=mysql: {exc}")

            conn = pymysql.connect(
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                user=os.getenv("MYSQL_USER", "root"),
                password=os.getenv("MYSQL_PASSWORD", ""),
                database=os.getenv("MYSQL_DATABASE", "carpool"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                autocommit=False,
            )
        else:
            conn = sqlite3.connect(DATABASE_PATH)

        cur = conn.cursor()

        if self.engine == "mysql":
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carpools (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    first_name VARCHAR(120) NOT NULL,
                    last_initial VARCHAR(1) NOT NULL,
                    phone VARCHAR(32) NOT NULL,
                    flight_code VARCHAR(16) NOT NULL,
                    airport_code VARCHAR(3) NOT NULL,
                    airport_name VARCHAR(255) NOT NULL,
                    airport_location VARCHAR(255) NOT NULL,
                    flight_time_utc VARCHAR(16) NOT NULL,
                    flight_date_utc VARCHAR(16) NOT NULL,
                    seats_available INT NOT NULL,
                    notes TEXT NOT NULL,
                    fetched_from VARCHAR(64) NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    expires_at VARCHAR(64) NOT NULL,
                    created_at VARCHAR(64) NOT NULL
                )
                """
            )
        else:
            cur.execute(
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
        cur.close()
        conn.close()


db = DBAdapter()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_flight_code(code: str) -> str:
    return "".join(code.upper().strip().split())


@app.teardown_appcontext
def close_db(_: Any) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _cleanup_expired_entries() -> None:
    p = db.placeholder
    db.execute(f"DELETE FROM carpools WHERE expires_at <= {p}", (_now_utc().isoformat(),))


def _fetch_from_opensky(flight_code: str) -> dict[str, str] | None:
    try:
        with urlopen("https://opensky-network.org/api/states/all", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return None

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
                "departure": state[2] or "Unknown",
                "destination": "Unknown",
                "expires_at": expires_at.isoformat(),
            }
    return {"status": "not_found_live", "source": "opensky"}


def _fetch_from_adsbx(flight_code: str) -> dict[str, str] | None:
    api_key = os.getenv("ADSBX_API_KEY", "").strip()
    if not api_key:
        return None

    endpoint = f"https://api.adsbexchange.com/v2/callsign/{flight_code}/"
    req = Request(endpoint, headers={"api-auth": api_key, "accept": "application/json"})
    try:
        with urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    aircraft = payload.get("ac") or []
    if not aircraft:
        return {"status": "not_found_live", "source": "adsbx"}

    item = aircraft[0]
    observed = _now_utc()
    expires_at = observed + timedelta(hours=6)
    return {
        "status": "airborne" if not item.get("gnd", False) else "on_ground",
        "source": "adsbx",
        "flight_time_utc": observed.strftime("%H:%M"),
        "flight_date_utc": observed.strftime("%Y-%m-%d"),
        "departure": item.get("from") or "Unknown",
        "destination": item.get("to") or "Unknown",
        "expires_at": expires_at.isoformat(),
    }


def _fetch_flight_live(flight_code: str) -> dict[str, str]:
    opensky = _fetch_from_opensky(flight_code)
    if opensky and opensky.get("status") != "not_found_live":
        return opensky

    adsbx = _fetch_from_adsbx(flight_code)
    if adsbx and adsbx.get("status") != "not_found_live":
        return adsbx

    return {"status": "not_found_live", "source": "unavailable"}


def _flight_suggestions(query: str) -> list[dict[str, str]]:
    cleaned = _clean_flight_code(query)
    if len(cleaned) < 2:
        return []

    suggestions: list[dict[str, str]] = []

    opensky = _fetch_from_opensky(cleaned)
    if opensky and opensky.get("status") not in {"not_found_live", "unknown"}:
        suggestions.append(
            {
                "flight_code": cleaned,
                "time_utc": opensky.get("flight_time_utc", "Unknown"),
                "departure": opensky.get("departure", "Unknown"),
                "destination": opensky.get("destination", "Unknown"),
                "status": opensky.get("status", "unknown"),
            }
        )

    adsbx = _fetch_from_adsbx(cleaned)
    if adsbx and adsbx.get("status") not in {"not_found_live", "unknown"}:
        suggestions.append(
            {
                "flight_code": cleaned,
                "time_utc": adsbx.get("flight_time_utc", "Unknown"),
                "departure": adsbx.get("departure", "Unknown"),
                "destination": adsbx.get("destination", "Unknown"),
                "status": adsbx.get("status", "unknown"),
            }
        )

    # de-dupe by flight_code/source content
    seen = set()
    unique: list[dict[str, str]] = []
    for item in suggestions:
        key = (item["flight_code"], item["time_utc"], item["departure"], item["destination"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:8]


def _resolve_airport(code: str) -> tuple[str, str]:
    airport = AIRPORT_CODE_MAP.get(code)
    if airport:
        return airport["name"], airport["location"]
    return f"Airport {code}", "Unknown location"


def _serialize_entry(row: dict[str, Any], include_phone: bool = False) -> dict[str, Any]:
    data = dict(row)
    if not include_phone:
        data.pop("phone", None)
    return data


def _require_admin() -> bool:
    return bool(session.get("admin_authed"))


@app.get("/")
def landing() -> Any:
    admin_error = request.args.get("admin_error") == "1"
    return render_template("welcome.html", admin_error=admin_error)


@app.get("/start-now")
def start_now_page() -> Any:
    return render_template("start_now.html")


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

    raw_flight_code = str(data["flight_code"]).strip().upper()
    if " " in raw_flight_code:
        return jsonify({"error": "flight_code must not contain spaces (use UA533 format)"}), 400

    flight_code = _clean_flight_code(raw_flight_code)
    if not FLIGHT_CODE_PATTERN.match(flight_code):
        return jsonify({"error": "Invalid flight_code format. Example: UA533"}), 400

    if not PHONE_PATTERN.match(str(data["phone"]).strip()):
        return jsonify({"error": "Phone must be in format +1 (AAA) BBB CCCC"}), 400

    flight_info = _fetch_flight_live(flight_code)
    if flight_info.get("status") == "not_found_live":
        return jsonify({"error": "Flight not found in live data. Pick a valid UA533-style code from suggestions."}), 400

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()
    expires_at = flight_info.get("expires_at") or (_now_utc() + timedelta(hours=12)).isoformat()

    p = db.placeholder
    last_id = db.execute(
        f"""
        INSERT INTO carpools (
            first_name,last_initial,phone,flight_code,airport_code,airport_name,airport_location,
            flight_time_utc,flight_date_utc,seats_available,notes,fetched_from,status,expires_at,created_at
        ) VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
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

    row = db.query(f"SELECT * FROM carpools WHERE id = {p}", (last_id,))[0]
    return jsonify({"message": "Added to carpool database", "entry": _serialize_entry(row)}), 201


@app.get("/api/flights/suggest")
def suggest_flights() -> Any:
    query = request.args.get("query", "")
    suggestions = _flight_suggestions(query)
    return jsonify({"query": _clean_flight_code(query), "count": len(suggestions), "results": suggestions})


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    _cleanup_expired_entries()
    flight_code = _clean_flight_code(request.args.get("flight_code", ""))
    airport_code = request.args.get("airport_code", "").upper().strip()

    rows = db.query("SELECT * FROM carpools ORDER BY created_at DESC")

    results: list[dict[str, Any]] = []
    for entry in rows:
        score = 0
        reasons: list[str] = []
        if flight_code and entry["flight_code"] == flight_code:
            score += 70
            reasons.append("Exact flight code match")
        if airport_code and entry["airport_code"] == airport_code:
            score += 30
            reasons.append("Same airport code")
        if score > 0 or (not flight_code and not airport_code):
            public_row = _serialize_entry(entry)
            public_row["match_score"] = score
            public_row["match_reasons"] = reasons
            results.append(public_row)

    results.sort(key=lambda r: r["match_score"], reverse=True)
    return jsonify({"count": len(results), "results": results})


@app.get("/api/carpools/<int:entry_id>")
def carpool_details(entry_id: int) -> Any:
    _cleanup_expired_entries()
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (entry_id,))
    if not rows:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"entry": _serialize_entry(rows[0], include_phone=True)})


@app.post("/admin/login")
def admin_login() -> Any:
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session["admin_authed"] = True
        return redirect(url_for("admin_panel"))
    return redirect(url_for("landing", admin_error=1))


@app.get("/admin")
def admin_panel() -> Any:
    if not _require_admin():
        return redirect(url_for("landing"))
    _cleanup_expired_entries()
    rows = db.query("SELECT * FROM carpools ORDER BY created_at DESC")
    return render_template("admin.html", entries=rows)


@app.post("/admin/delete-all")
def admin_delete_all() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    db.execute("DELETE FROM carpools")
    return redirect(url_for("admin_panel"))


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    p = db.placeholder
    db.execute(f"DELETE FROM carpools WHERE id = {p}", (entry_id,))
    return redirect(url_for("admin_panel"))


@app.post("/admin/edit/<int:entry_id>")
def admin_edit_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401

    first_name = request.form.get("first_name", "").strip().title()
    last_initial = request.form.get("last_initial", "").strip().upper()[:1]
    phone = request.form.get("phone", "").strip()
    notes = request.form.get("notes", "").strip()

    p = db.placeholder
    db.execute(
        f"""
        UPDATE carpools
        SET first_name = COALESCE(NULLIF({p}, ''), first_name),
            last_initial = COALESCE(NULLIF({p}, ''), last_initial),
            phone = COALESCE(NULLIF({p}, ''), phone),
            notes = COALESCE(NULLIF({p}, ''), notes)
        WHERE id = {p}
        """,
        (first_name, last_initial, phone, notes, entry_id),
    )
    return redirect(url_for("admin_panel"))


try:
    # Ensure sqlite dir exists if sqlite mode.
    if DB_ENGINE == "sqlite" and DATABASE_PATH not in (":memory:",):
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    db.init_schema()
except Exception as exc:
    print(f"[startup] database initialization failed: {exc}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
