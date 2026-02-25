from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Keshavpsn!8")
FLIGHT_CODE_PATTERN = re.compile(r"^[A-Z]{2,3}\d{1,4}[A-Z]?$")
PHONE_PATTERN = re.compile(r"^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$")

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", os.getenv("SERPAPI_KEY", ""))
SERPAPI_ENDPOINT = os.getenv("SERPAPI_ENDPOINT", "https://serpapi.com/search.json")
SERPAPI_TIMEOUT = float(os.getenv("SERPAPI_TIMEOUT_SECONDS", "8"))


def _resolve_database_path() -> str:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "/tmp/carpool.db"
    return "carpool.db"


DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()
DATABASE_PATH = _resolve_database_path()

# Expanded airport list
AIRPORT_CODE_MAP = {
    "SFO": {"name": "San Francisco International Airport", "location": "San Francisco, CA"},
    "ONT": {"name": "Ontario International Airport", "location": "Ontario, CA"},
    "LAX": {"name": "Los Angeles International Airport", "location": "Los Angeles, CA"},
    "JFK": {"name": "John F. Kennedy International Airport", "location": "New York, NY"},
    "EWR": {"name": "Newark Liberty International Airport", "location": "Newark, NJ"},
    "ORD": {"name": "O'Hare International Airport", "location": "Chicago, IL"},
    "ATL": {"name": "Hartsfield-Jackson Atlanta International Airport", "location": "Atlanta, GA"},
    "DFW": {"name": "Dallas/Fort Worth International Airport", "location": "Dallas, TX"},
    "DEN": {"name": "Denver International Airport", "location": "Denver, CO"},
    "SEA": {"name": "Seattle-Tacoma International Airport", "location": "Seattle, WA"},
    "LAS": {"name": "Harry Reid International Airport", "location": "Las Vegas, NV"},
    "MCO": {"name": "Orlando International Airport", "location": "Orlando, FL"},
    "MIA": {"name": "Miami International Airport", "location": "Miami, FL"},
    "PHX": {"name": "Phoenix Sky Harbor International Airport", "location": "Phoenix, AZ"},
    "IAH": {"name": "George Bush Intercontinental Airport", "location": "Houston, TX"},
    "BOS": {"name": "Boston Logan International Airport", "location": "Boston, MA"},
    "MSP": {"name": "Minneapolis-Saint Paul International Airport", "location": "Minneapolis, MN"},
    "DTW": {"name": "Detroit Metropolitan Wayne County Airport", "location": "Detroit, MI"},
    "PHL": {"name": "Philadelphia International Airport", "location": "Philadelphia, PA"},
    "CLT": {"name": "Charlotte Douglas International Airport", "location": "Charlotte, NC"},
    "SAN": {"name": "San Diego International Airport", "location": "San Diego, CA"},
    "SJC": {"name": "San Jose International Airport", "location": "San Jose, CA"},
    "IAD": {"name": "Washington Dulles International Airport", "location": "Washington, DC"},
    "DCA": {"name": "Ronald Reagan Washington National Airport", "location": "Washington, DC"},
    "BUR": {"name": "Hollywood Burbank Airport", "location": "Burbank, CA"},
    "SNA": {"name": "John Wayne Airport", "location": "Santa Ana, CA"},
    "OAK": {"name": "Oakland International Airport", "location": "Oakland, CA"},
    "PDX": {"name": "Portland International Airport", "location": "Portland, OR"},
    "TPA": {"name": "Tampa International Airport", "location": "Tampa, FL"},
    "FLL": {"name": "Fort Lauderdale-Hollywood International Airport", "location": "Fort Lauderdale, FL"},
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
            except Exception as exc:
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
            import pymysql

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
                    created_at VARCHAR(64) NOT NULL,
                    requested_flight_date VARCHAR(16) NOT NULL,
                    destination_airport VARCHAR(3) NOT NULL DEFAULT ''
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
                    created_at TEXT NOT NULL,
                    requested_flight_date TEXT NOT NULL,
                    destination_airport TEXT NOT NULL DEFAULT ''
                )
                """
            )

        conn.commit()
        cur.close()
        conn.close()

    def ensure_columns(self) -> None:
        conn = self.get_conn()
        cur = conn.cursor()
        try:
            if self.engine == "mysql":
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'requested_flight_date'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN requested_flight_date VARCHAR(16) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'destination_airport'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN destination_airport VARCHAR(3) NOT NULL DEFAULT ''")
            else:
                cur.execute("PRAGMA table_info(carpools)")
                cols = [row[1] for row in cur.fetchall()]
                if "requested_flight_date" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN requested_flight_date TEXT NOT NULL DEFAULT ''")
                if "destination_airport" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN destination_airport TEXT NOT NULL DEFAULT ''")
            conn.commit()
        finally:
            cur.close()


db = DBAdapter()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_flight_code(code: str) -> str:
    return "".join(code.upper().strip().split())


def _parse_user_flight_date(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _to_user_flight_date(dt: datetime) -> str:
    return dt.strftime("%m-%d-%Y")


def _to_api_flight_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# SerpApi Google Flights integration
# ---------------------------------------------------------------------------
# NOTE: SerpApi google_flights engine searches by route (departure_id +
# arrival_id), NOT by flight number.  We use it to find flights on a given
# date from a given airport pair, which is what the suggest endpoint returns.
# For flight-number-based lookup we extract matching legs from the results.
# ---------------------------------------------------------------------------

def _serpapi_search_flights(departure_id: str, arrival_id: str, outbound_date: str) -> dict[str, Any]:
    """Search SerpApi Google Flights by route and date."""
    if not SERPAPI_API_KEY:
        return {}

    params = {
        "engine": "google_flights",
        "hl": "en",
        "gl": "us",
        "type": "2",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "currency": "USD",
        "api_key": SERPAPI_API_KEY,
    }
    url = f"{SERPAPI_ENDPOINT}?{urlencode(params)}"
    req = Request(url, headers={"accept": "application/json"})

    try:
        with urlopen(req, timeout=SERPAPI_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        return {}

    return payload if isinstance(payload, dict) else {}


def _extract_flights_from_serpapi(payload: dict, flight_code_filter: str | None = None) -> list[dict[str, str]]:
    """Extract normalized flight info from SerpApi response."""
    collected: list[dict[str, str]] = []

    for bucket in ("best_flights", "other_flights", "flights"):
        items = payload.get(bucket) or []
        if not isinstance(items, list):
            continue
        for itinerary in items:
            if not isinstance(itinerary, dict):
                continue
            flights = itinerary.get("flights") or []
            if not isinstance(flights, list) or not flights:
                continue

            first = flights[0] if isinstance(flights[0], dict) else {}
            departure = first.get("departure_airport") or {}
            arrival = first.get("arrival_airport") or {}
            airline = first.get("airline") or ""
            flight_number = first.get("flight_number") or ""

            full_code = f"{airline}{flight_number}".replace(" ", "").upper() if airline and flight_number else ""
            if not full_code:
                # Try to build from other fields
                operating = first.get("operating_airline") or ""
                if operating and flight_number:
                    full_code = f"{operating}{flight_number}".replace(" ", "").upper()

            dep_code = departure.get("id") or ""
            arr_code = arrival.get("id") or ""

            raw_time = departure.get("time") or ""
            dep_date = ""
            dep_time = ""
            if raw_time:
                try:
                    dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    dep_time = dt.strftime("%H:%M")
                    dep_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass

            # If filtering by flight code, check match
            if flight_code_filter:
                clean_filter = _clean_flight_code(flight_code_filter)
                if full_code and clean_filter not in full_code and full_code not in clean_filter:
                    continue

            entry = {
                "flight_code": full_code or (flight_code_filter or "UNKNOWN"),
                "airline": airline,
                "time_utc": dep_time or "TBD",
                "date_utc": dep_date or "",
                "departure": dep_code,
                "departure_name": departure.get("name") or "",
                "destination": arr_code,
                "destination_name": arrival.get("name") or "",
                "status": str(itinerary.get("type") or "scheduled").lower(),
                "source": "serpapi_google_flights",
                "duration": str(first.get("duration") or ""),
            }
            collected.append(entry)

    return collected


def _lookup_present_or_future_flights(flight_code: str, flight_date_user: str | None = None) -> list[dict[str, str]]:
    """Look up flights. Returns a list of matching flights."""
    if not SERPAPI_API_KEY:
        return []

    dates_api: list[str] = []
    if flight_date_user:
        parsed = _parse_user_flight_date(flight_date_user)
        if parsed:
            dates_api.append(_to_api_flight_date(parsed))
        else:
            return []
    else:
        base = _now_utc().date()
        dates_api.extend([(base + timedelta(days=i)).isoformat() for i in range(0, 3)])

    # Try common airport pairs for the airline
    airline_prefix = re.match(r"^[A-Z]{2,3}", flight_code)
    if not airline_prefix:
        return []

    # Use major US hub pairs for searching
    hub_pairs = [
        ("SFO", "JFK"), ("SFO", "LAX"), ("SFO", "ORD"), ("SFO", "EWR"),
        ("LAX", "JFK"), ("LAX", "ORD"), ("LAX", "SFO"), ("LAX", "EWR"),
        ("JFK", "LAX"), ("JFK", "SFO"), ("JFK", "ORD"), ("JFK", "MIA"),
        ("ORD", "LAX"), ("ORD", "JFK"), ("ORD", "SFO"),
    ]

    collected: list[dict[str, str]] = []
    for date_item in dates_api:
        for dep, arr in hub_pairs[:6]:  # Limit API calls
            if collected:  # Stop once we found something
                break
            payload = _serpapi_search_flights(dep, arr, date_item)
            if payload:
                results = _extract_flights_from_serpapi(payload, flight_code)
                collected.extend(results)

    # Deduplicate
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in collected:
        key = (item["flight_code"], item["date_utc"], item["time_utc"], item["destination"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique[:10]


def _fetch_flight_live_or_future(flight_code: str, flight_date_user: str | None = None) -> dict[str, str]:
    results = _lookup_present_or_future_flights(flight_code, flight_date_user)
    if not results:
        return {"status": "unverified", "source": "user_entered"}

    best = results[0]
    expires_at = (_now_utc() + timedelta(hours=6)).isoformat()
    return {
        "status": best.get("status", "scheduled"),
        "source": best["source"],
        "flight_time_utc": best["time_utc"],
        "flight_date_utc": best["date_utc"],
        "departure": best["departure"],
        "destination": best["destination"],
        "expires_at": expires_at,
    }


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


@app.teardown_appcontext
def close_db(_: Any) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _cleanup_expired_entries() -> None:
    p = db.placeholder
    db.execute(f"DELETE FROM carpools WHERE expires_at != '' AND expires_at <= {p}", (_now_utc().isoformat(),))


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/carpools")
def create_carpool() -> Any:
    _cleanup_expired_entries()
    data = request.get_json(silent=True) or request.form.to_dict()

    required = ["first_name", "last_initial", "phone", "flight_code", "airport_code"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if not str(data.get("departure_date") or data.get("flight_date") or "").strip():
        missing.append("departure_date")
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

    departure_date_raw = str(data.get("departure_date") or data.get("flight_date") or "").strip()
    parsed_flight_date = _parse_user_flight_date(departure_date_raw)
    if not parsed_flight_date:
        return jsonify({"error": "departure_date must be in MM-DD-YYYY format"}), 400
    flight_date_user = _to_user_flight_date(parsed_flight_date)

    # Try to fetch live flight data, but save regardless
    flight_info = _fetch_flight_live_or_future(flight_code, flight_date_user)
    warning = None
    if flight_info.get("source") == "user_entered":
        warning = "Flight could not be verified via API. Saved with user-provided details."

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()
    # For unverified flights, set a longer expiration (24 hours)
    expires_at = flight_info.get("expires_at") or (_now_utc() + timedelta(hours=24)).isoformat()

    destination_airport = str(data.get("destination_airport") or flight_info.get("destination") or "").strip().upper()[:3]

    p = db.placeholder
    last_id = db.execute(
        f"""
        INSERT INTO carpools (
            first_name,last_initial,phone,flight_code,airport_code,airport_name,airport_location,
            flight_time_utc,flight_date_utc,seats_available,notes,fetched_from,status,expires_at,created_at,
            requested_flight_date,destination_airport
        ) VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
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
            flight_info.get("flight_date_utc", _to_api_flight_date(parsed_flight_date)),
            int(data.get("seats_available", 3) or 3),
            str(data.get("notes", "")).strip(),
            flight_info.get("source", "user_entered"),
            flight_info.get("status", "unverified"),
            expires_at,
            created_at,
            flight_date_user,
            destination_airport,
        ),
    )

    row = db.query(f"SELECT * FROM carpools WHERE id = {p}", (last_id,))[0]
    response = {"message": "Added to carpool database", "entry": _serialize_entry(row)}
    if warning:
        response["warning"] = warning
    return jsonify(response), 201


@app.get("/api/flights/suggest")
def suggest_flights() -> Any:
    query = request.args.get("query", "")
    cleaned = _clean_flight_code(query)
    departure_date = (request.args.get("departure_date", "") or request.args.get("flight_date", "")).strip() or None
    suggestions = _lookup_present_or_future_flights(cleaned, departure_date)
    return jsonify({"query": cleaned, "count": len(suggestions), "results": suggestions})


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    _cleanup_expired_entries()
    flight_code = _clean_flight_code(request.args.get("flight_code", ""))
    airport_code = request.args.get("airport_code", "").upper().strip()
    flight_date_raw = (request.args.get("departure_date", "") or request.args.get("flight_date", "")).strip()
    parsed_search_date = _parse_user_flight_date(flight_date_raw) if flight_date_raw else None
    flight_date = _to_user_flight_date(parsed_search_date) if parsed_search_date else ""

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
        if flight_date and entry.get("requested_flight_date") == flight_date:
            score += 20
            reasons.append("Same requested flight date")
        if score > 0 or (not flight_code and not airport_code and not flight_date):
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


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

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
    flight_code = request.form.get("flight_code", "").strip().upper()
    airport_code = request.form.get("airport_code", "").strip().upper()
    seats = request.form.get("seats_available", "").strip()

    p = db.placeholder
    db.execute(
        f"""
        UPDATE carpools
        SET first_name = COALESCE(NULLIF({p}, ''), first_name),
            last_initial = COALESCE(NULLIF({p}, ''), last_initial),
            phone = COALESCE(NULLIF({p}, ''), phone),
            notes = COALESCE(NULLIF({p}, ''), notes),
            flight_code = COALESCE(NULLIF({p}, ''), flight_code),
            airport_code = COALESCE(NULLIF({p}, ''), airport_code),
            seats_available = COALESCE(NULLIF({p}, ''), seats_available)
        WHERE id = {p}
        """,
        (first_name, last_initial, phone, notes, flight_code, airport_code, seats, entry_id),
    )
    return redirect(url_for("admin_panel"))


@app.get("/admin/logout")
def admin_logout() -> Any:
    session.pop("admin_authed", None)
    return redirect(url_for("landing"))


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

try:
    if DB_ENGINE == "sqlite" and DATABASE_PATH not in (":memory:",):
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    with app.app_context():
        db.init_schema()
        db.ensure_columns()
except Exception as exc:
    print(f"[startup] database initialization failed: {exc}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
