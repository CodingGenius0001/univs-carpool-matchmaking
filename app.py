from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import sqlite3
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import traceback

# Load .env file for local development (silently ignored if not present or
# if python-dotenv is not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("Keshavpsn!8")
FLIGHT_CODE_PATTERN = re.compile(r"^[A-Z]{2,3}\d{1,4}[A-Z]?$")
PHONE_PATTERN = re.compile(r"^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$")
NAME_PATTERN = re.compile(r"^[A-Za-z \-']+$")

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", os.getenv("SERPAPI_KEY", ""))
SERPAPI_ENDPOINT = os.getenv("SERPAPI_ENDPOINT", "https://serpapi.com/search.json")
SERPAPI_TIMEOUT = float(os.getenv("SERPAPI_TIMEOUT_SECONDS", "4"))

# Airline IATA codes for autocomplete
AIRLINE_CODES: dict[str, str] = {
    "AA": "American Airlines", "UA": "United Airlines", "DL": "Delta Air Lines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways", "AS": "Alaska Airlines",
    "NK": "Spirit Airlines", "F9": "Frontier Airlines", "G4": "Allegiant Air",
    "SY": "Sun Country Airlines", "HA": "Hawaiian Airlines",
    "BA": "British Airways", "LH": "Lufthansa", "AF": "Air France",
    "KL": "KLM Royal Dutch", "AC": "Air Canada", "QF": "Qantas",
    "EK": "Emirates", "QR": "Qatar Airways", "SQ": "Singapore Airlines",
    "CX": "Cathay Pacific", "NH": "ANA", "JL": "Japan Airlines",
    "TK": "Turkish Airlines", "LX": "SWISS", "IB": "Iberia",
    "VS": "Virgin Atlantic", "AM": "Aeromexico", "AV": "Avianca",
    "LA": "LATAM Airlines", "CM": "Copa Airlines", "WS": "WestJet",
    "EI": "Aer Lingus", "SK": "SAS Scandinavian", "AY": "Finnair",
    "OS": "Austrian Airlines", "TP": "TAP Air Portugal",
    "MX": "Breeze Airways", "QX": "Horizon Air",
    "OO": "SkyWest Airlines", "YX": "Republic Airways",
    "9K": "Cape Air", "MQ": "Envoy Air",
}

# Airline hub airports for smarter flight suggestions
AIRLINE_HUBS: dict[str, list[str]] = {
    "AA": ["DFW", "CLT", "MIA", "PHL", "ORD"],
    "UA": ["ORD", "EWR", "IAH", "SFO", "DEN"],
    "DL": ["ATL", "MSP", "DTW", "JFK", "LAX"],
    "WN": ["LAS", "DEN", "PHX", "MCO"],
    "B6": ["JFK", "BOS", "FLL", "MCO"],
    "AS": ["SEA", "PDX", "SFO", "LAX"],
    "NK": ["FLL", "LAS", "DTW", "MCO"],
    "F9": ["DEN", "LAS", "MCO"],
    "G4": ["LAS", "SFO", "LAX"],
    "SY": ["MSP", "DFW"],
    "HA": ["HNL", "LAX", "SFO"],
    "MX": ["TPA", "MCO", "CHS", "RIC", "BDL", "MSY"],
}


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


def _mysql_needs_ssl() -> bool:
    """Auto-detect whether the MySQL host requires SSL (e.g. TiDB Cloud)."""
    if os.getenv("MYSQL_SSL", "").lower() in ("1", "true", "yes"):
        return True
    host = os.getenv("MYSQL_HOST", "")
    return "tidbcloud.com" in host or "aivencloud.com" in host


def _mysql_ssl_ctx() -> ssl.SSLContext | None:
    if not _mysql_needs_ssl():
        return None
    return ssl.create_default_context()


class DBAdapter:
    def __init__(self) -> None:
        self.engine = DB_ENGINE
        self.placeholder = "%s" if self.engine == "mysql" else "?"
        self._mysql_failed = False
        self._mysql_failed_at: float | None = None  # timestamp of last failure

    def _activate_sqlite_fallback(self, reason: str = "") -> None:
        """Switch to SQLite fallback. Retries MySQL after 60 s of downtime."""
        import time
        if not self._mysql_failed:
            app.logger.warning(f"Switching to SQLite fallback. {reason}")
        self._mysql_failed = True
        self._mysql_failed_at = time.time()
        self.placeholder = "?"
        # Discard any broken MySQL connection on this request
        old = g.pop("db", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass

    def _should_retry_mysql(self) -> bool:
        """Return True if enough time has passed to retry MySQL after a failure."""
        import time
        if not self._mysql_failed:
            return False
        if self._mysql_failed_at is None:
            return False
        return (time.time() - self._mysql_failed_at) > 60

    def _get_sqlite_conn(self) -> Any:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
        # WAL mode: allows concurrent readers with one writer, more reliable
        # on mobile/serverless where requests may overlap
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        return g.db

    def get_conn(self) -> Any:
        if "db" in g:
            return g.db

        # Reset failure flag after 60 s so transient MySQL outages self-heal
        if self._should_retry_mysql():
            app.logger.info("Retrying MySQL connection after cooldown.")
            self._mysql_failed = False
            self._mysql_failed_at = None
            self.placeholder = "%s"

        if self.engine == "mysql" and not self._mysql_failed:
            try:
                import pymysql
                from pymysql.cursors import DictCursor
            except Exception as exc:
                raise RuntimeError(f"PyMySQL is required for DB_ENGINE=mysql: {exc}")

            try:
                connect_kwargs: dict[str, Any] = dict(
                    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", ""),
                    database=os.getenv("MYSQL_DATABASE", "carpool"),
                    port=int(os.getenv("MYSQL_PORT", "3306")),
                    autocommit=False,
                    cursorclass=DictCursor,
                    connect_timeout=5,
                    read_timeout=10,
                    write_timeout=10,
                )
                ssl_ctx = _mysql_ssl_ctx()
                if ssl_ctx:
                    connect_kwargs["ssl"] = ssl_ctx
                g.db = pymysql.connect(**connect_kwargs)
                return g.db
            except Exception as exc:
                mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1")
                self._activate_sqlite_fallback(
                    f"MySQL connection failed to {mysql_host}: {exc}. "
                    "If using InfinityFree or similar shared hosting, their MySQL "
                    "only accepts connections from their own servers — not from "
                    "Vercel/external services."
                )

        return self._get_sqlite_conn()

    def _is_mysql_conn_error(self, exc: Exception) -> bool:
        """Check if an exception is a MySQL connection/operational error."""
        try:
            import pymysql
            return isinstance(exc, (pymysql.OperationalError, pymysql.InterfaceError, OSError))
        except ImportError:
            return False

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL query failed: {exc}")
                conn = self._get_sqlite_conn()
                cur = conn.cursor()
                cur.execute(sql.replace("%s", "?"), params)
                rows = cur.fetchall()
                cur.close()
            else:
                raise
        if self._mysql_failed or self.engine == "sqlite":
            return [dict(r) for r in rows]
        return list(rows)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(sql, params)
            last_id = getattr(cur, "lastrowid", 0)
            conn.commit()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL execute failed: {exc}")
                conn = self._get_sqlite_conn()
                cur = conn.cursor()
                cur.execute(sql.replace("%s", "?"), params)
                last_id = getattr(cur, "lastrowid", 0)
                conn.commit()
                cur.close()
            else:
                raise
        return int(last_id or 0)

    def init_schema(self) -> None:
        use_mysql = self.engine == "mysql" and not self._mysql_failed

        if use_mysql:
            try:
                import pymysql
                init_kwargs: dict[str, Any] = dict(
                    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", ""),
                    database=os.getenv("MYSQL_DATABASE", "carpool"),
                    port=int(os.getenv("MYSQL_PORT", "3306")),
                    autocommit=False,
                    connect_timeout=5,
                )
                ssl_ctx = _mysql_ssl_ctx()
                if ssl_ctx:
                    init_kwargs["ssl"] = ssl_ctx
                conn = pymysql.connect(**init_kwargs)
            except Exception as exc:
                mysql_host = os.getenv("MYSQL_HOST", "127.0.0.1")
                app.logger.error(
                    f"MySQL init_schema failed ({mysql_host}): {exc}. Falling back to SQLite."
                )
                self._mysql_failed = True
                self.placeholder = "?"
                use_mysql = False

        if not use_mysql:
            conn = sqlite3.connect(DATABASE_PATH)

        cur = conn.cursor()
        if use_mysql:
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
                    destination_airport VARCHAR(3) NOT NULL DEFAULT '',
                    planned_departure_time VARCHAR(16) NOT NULL DEFAULT '',
                    creator_email VARCHAR(255) NOT NULL DEFAULT ''
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
                    destination_airport TEXT NOT NULL DEFAULT '',
                    planned_departure_time TEXT NOT NULL DEFAULT '',
                    creator_email TEXT NOT NULL DEFAULT ''
                )
                """
            )

        # Create party_members table
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS party_members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    carpool_id INT NOT NULL,
                    user_email VARCHAR(255) NOT NULL,
                    joined_at VARCHAR(64) NOT NULL,
                    UNIQUE KEY uq_carpool_member (carpool_id, user_email)
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS party_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    carpool_id INTEGER NOT NULL,
                    user_email TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    UNIQUE(carpool_id, user_email)
                )
                """
            )

        # Create users table for storing profile info (name, phone)
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_email VARCHAR(255) PRIMARY KEY,
                    first_name VARCHAR(120) NOT NULL DEFAULT '',
                    last_initial VARCHAR(1) NOT NULL DEFAULT '',
                    phone VARCHAR(32) NOT NULL DEFAULT '',
                    created_at VARCHAR(64) NOT NULL DEFAULT ''
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_email TEXT PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    last_initial TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )

        # Create notifications table for disband messages etc.
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_email VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    created_at VARCHAR(64) NOT NULL,
                    dismissed INT NOT NULL DEFAULT 0
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    dismissed INTEGER NOT NULL DEFAULT 0
                )
                """
            )

        conn.commit()
        cur.close()
        conn.close()

    def ensure_columns(self) -> None:
        conn = self.get_conn()
        cur = conn.cursor()
        use_mysql = self.engine == "mysql" and not self._mysql_failed
        try:
            if use_mysql:
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'requested_flight_date'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN requested_flight_date VARCHAR(16) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'destination_airport'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN destination_airport VARCHAR(3) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'planned_departure_time'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN planned_departure_time VARCHAR(16) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'creator_email'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN creator_email VARCHAR(255) NOT NULL DEFAULT ''")
            else:
                cur.execute("PRAGMA table_info(carpools)")
                cols = [row[1] for row in cur.fetchall()]
                if "requested_flight_date" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN requested_flight_date TEXT NOT NULL DEFAULT ''")
                if "destination_airport" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN destination_airport TEXT NOT NULL DEFAULT ''")
                if "planned_departure_time" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN planned_departure_time TEXT NOT NULL DEFAULT ''")
                if "creator_email" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN creator_email TEXT NOT NULL DEFAULT ''")
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


def _build_hub_pairs(airline_prefix: str) -> list[tuple[str, str]]:
    """Build hub pairs prioritizing airline-specific hubs."""
    major_dests = ["JFK", "LAX", "ORD", "SFO", "ATL", "DEN", "MCO", "SEA"]
    pairs: list[tuple[str, str]] = []

    # Add airline-specific hub pairs first
    hubs = AIRLINE_HUBS.get(airline_prefix, [])
    for hub in hubs[:3]:
        for dest in major_dests:
            if dest != hub:
                pairs.append((hub, dest))
                break

    # Add generic hub pairs as fallback
    generic = [
        ("SFO", "JFK"), ("LAX", "JFK"), ("ORD", "LAX"), ("ATL", "LAX"),
        ("DFW", "JFK"), ("DEN", "ORD"), ("SEA", "LAX"), ("BOS", "MIA"),
        ("MCO", "JFK"), ("JFK", "SFO"), ("EWR", "LAX"), ("MIA", "ORD"),
    ]
    for pair in generic:
        if pair not in pairs:
            pairs.append(pair)

    return pairs


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

    airline_prefix = re.match(r"^[A-Z]{2,3}", flight_code)
    if not airline_prefix:
        return []

    hub_pairs = _build_hub_pairs(airline_prefix.group())

    collected: list[dict[str, str]] = []
    for date_item in dates_api:
        if collected:
            break
        for dep, arr in hub_pairs[:4]:
            if collected:
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


def _suggest_flights_for_airline(flight_code: str, flight_date_user: str | None = None) -> list[dict[str, str]]:
    """Suggest flights matching an airline prefix. Less strict than verification lookup."""
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
        dates_api.append(base.isoformat())

    airline_prefix = re.match(r"^[A-Z]{2,3}", flight_code)
    if not airline_prefix:
        return []
    prefix = airline_prefix.group()

    hub_pairs = _build_hub_pairs(prefix)

    collected: list[dict[str, str]] = []
    for date_item in dates_api:
        for dep, arr in hub_pairs[:5]:
            payload = _serpapi_search_flights(dep, arr, date_item)
            if payload:
                # Filter by airline prefix only for broader results
                results = _extract_flights_from_serpapi(payload, prefix)
                collected.extend(results)
            if len(collected) >= 10:
                break
        if collected:
            break

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
    if not session.get("admin_authed"):
        return False
    login_time = session.get("admin_login_at")
    if not login_time:
        session.pop("admin_authed", None)
        return False
    try:
        login_dt = datetime.fromisoformat(login_time)
        if _now_utc() - login_dt > timedelta(minutes=30):
            session.pop("admin_authed", None)
            session.pop("admin_login_at", None)
            return False
    except (ValueError, TypeError):
        session.pop("admin_authed", None)
        return False
    return True


@app.teardown_appcontext
def close_db(_: Any) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


_db_initialized = False


@app.before_request
def _ensure_db() -> None:
    """Ensure database table exists on every request (handles Vercel cold starts)."""
    global _db_initialized
    if _db_initialized:
        return
    try:
        db.init_schema()
        db.ensure_columns()
        _db_initialized = True
    except Exception:
        pass


@app.errorhandler(Exception)
def handle_exception(e: Exception) -> Any:
    """Return JSON errors for API routes, HTML for pages."""
    tb = traceback.format_exc()
    app.logger.error(f"Unhandled exception: {e}\n{tb}")
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Server error: {e}"}), 500
    return f"<h1>Internal Server Error</h1><pre>{e}</pre>", 500


def _cleanup_expired_entries() -> None:
    try:
        p = db.placeholder
        now_iso = _now_utc().isoformat()
        # Find expired carpools first so we can clean up party_members too
        expired = db.query(f"SELECT id FROM carpools WHERE expires_at != '' AND expires_at <= {p}", (now_iso,))
        if expired:
            for row in expired:
                db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (row["id"],))
            db.execute(f"DELETE FROM carpools WHERE expires_at != '' AND expires_at <= {p}", (now_iso,))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.get("/")
def landing() -> Any:
    return render_template("welcome.html")


def _require_user_login() -> bool:
    """Check if a regular user is logged in via Firebase."""
    return bool(session.get("user_email"))


def _user_context() -> dict[str, Any]:
    """Build common template context for logged-in pages."""
    email = session.get("user_email", "")
    has_party = False
    if email:
        try:
            p = db.placeholder
            rows = db.query(f"SELECT COUNT(*) as c FROM party_members WHERE user_email = {p}", (email,))
            has_party = rows[0]["c"] > 0 if rows else False
        except Exception:
            pass
    return {"user_email": email, "has_party": has_party}


@app.get("/start-now")
def start_now_page() -> Any:
    if not _require_user_login():
        return redirect(url_for("login_page"))
    return render_template("start_now.html", **_user_context())


@app.get("/landing")
def landing_legacy() -> Any:
    return redirect(url_for("landing"), code=302)


@app.get("/create-a-carpool")
def create_a_carpool_page() -> Any:
    if not _require_user_login():
        return redirect(url_for("login_page"))
    return render_template("create_a_carpool.html", **_user_context())


@app.get("/add-flight-details")
def add_flight_details_redirect() -> Any:
    """Redirect old URL to new one for backwards compatibility."""
    return redirect(url_for("create_a_carpool_page"), code=301)


@app.get("/find-a-carpool")
def find_a_carpool_page() -> Any:
    if not _require_user_login():
        return redirect(url_for("login_page"))
    return render_template("find_a_carpool.html", **_user_context())


@app.get("/my-party")
def my_party_page() -> Any:
    if not _require_user_login():
        return redirect(url_for("login_page"))
    return render_template("my_party.html", **_user_context())


@app.get("/join")
def join_page() -> Any:
    return redirect(url_for("create_a_carpool_page"), code=302)


@app.get("/eula")
def eula_page() -> Any:
    return render_template("eula.html", **_user_context())


@app.get("/privacy")
def privacy_page() -> Any:
    return render_template("privacy_policy.html", **_user_context())


@app.get("/docs/<path:filename>")
def serve_docs(filename: str) -> Any:
    docs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    return send_from_directory(docs_dir, filename, mimetype="application/pdf")


@app.get("/login")
def login_page() -> Any:
    if session.get("user_email"):
        return redirect(url_for("start_now_page"))
    error = request.args.get("error", "")
    return render_template("login.html", error=error)


@app.post("/auth/firebase-callback")
def firebase_callback() -> Any:
    """Receive Firebase ID token from client, verify email domain, set session."""
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    uid = str(data.get("uid", "")).strip()

    if not email or not uid:
        return jsonify({"error": "Missing authentication data"}), 400

    if not email.endswith("@ucr.edu"):
        return jsonify({"error": "Only @ucr.edu accounts are allowed"}), 403

    session.permanent = True
    session["user_email"] = email
    session["user_name"] = name
    session["user_uid"] = uid

    # Store/update user profile from Google display name
    if name:
        parts = name.strip().split()
        first_name = parts[0] if parts else ""
        last_initial = parts[-1][0].upper() if len(parts) > 1 and parts[-1] else ""
        p = db.placeholder
        try:
            existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
            if not existing:
                db.execute(
                    f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                    (email, first_name, last_initial, "", _now_utc().isoformat()),
                )
            else:
                # Update name in case Google name changed, but don't overwrite phone
                db.execute(
                    f"UPDATE users SET first_name = {p}, last_initial = {p} WHERE user_email = {p}",
                    (first_name, last_initial, email),
                )
        except Exception:
            pass

    return jsonify({"ok": True, "redirect": url_for("start_now_page")})


@app.get("/auth/logout")
def user_logout() -> Any:
    session.pop("user_email", None)
    session.pop("user_name", None)
    session.pop("user_uid", None)
    return redirect(url_for("landing"))


@app.get("/search")
def search_page() -> Any:
    return redirect(url_for("find_a_carpool_page"), code=302)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/carpools")
def create_carpool() -> Any:
    try:
        return _create_carpool_inner()
    except Exception as e:
        app.logger.error(f"create_carpool error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Server error: {e}"}), 500


def _create_carpool_inner() -> Any:
    _cleanup_expired_entries()
    data = request.get_json(silent=True) or request.form.to_dict()

    required = ["first_name", "last_initial", "phone", "flight_code", "airport_code"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if not str(data.get("departure_date") or data.get("flight_date") or "").strip():
        missing.append("departure_date")
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    first_name = data["first_name"].strip()
    if not NAME_PATTERN.match(first_name):
        return jsonify({"error": "First name can only contain letters, spaces, hyphens, and apostrophes"}), 400

    last_initial = data["last_initial"].strip()
    if len(last_initial) != 1 or not last_initial.isalpha():
        return jsonify({"error": "Last initial must be exactly 1 letter"}), 400

    airport_code = data["airport_code"].upper().strip()
    if len(airport_code) != 3:
        return jsonify({"error": "airport_code must be a 3-letter airport code"}), 400

    raw_flight_code = str(data["flight_code"]).strip().upper()
    if " " in raw_flight_code:
        return jsonify({"error": "flight_code must not contain spaces (use UA533 format)"}), 400

    flight_code = _clean_flight_code(raw_flight_code)
    if not FLIGHT_CODE_PATTERN.match(flight_code):
        return jsonify({"error": "Invalid flight_code format. Example: UA533"}), 400


    # Normalize phone: collapse all whitespace types (including non-breaking
    # spaces from mobile browsers) into single regular spaces, then strip.
    raw_phone = str(data["phone"])
    raw_phone = re.sub(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+", " ", raw_phone).strip()
    if not PHONE_PATTERN.match(raw_phone):
        return jsonify({"error": "Phone must be in format +1 (AAA) BBB CCCC"}), 400

    departure_date_raw = str(data.get("departure_date") or data.get("flight_date") or "").strip()
    parsed_flight_date = _parse_user_flight_date(departure_date_raw)
    if not parsed_flight_date:
        return jsonify({"error": "departure_date must be in MM-DD-YYYY format"}), 400
    flight_date_user = _to_user_flight_date(parsed_flight_date)

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()

    # Expire at 11:59 PM UTC on the departure date
    expires_at = parsed_flight_date.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).isoformat()

    destination_airport = str(data.get("destination_airport", "")).strip().upper()[:3]
    planned_departure_time = str(data.get("planned_departure_time", "")).strip()
    creator_email = session.get("user_email", "")

    p = db.placeholder
    last_id = db.execute(
        f"""
        INSERT INTO carpools (
            first_name,last_initial,phone,flight_code,airport_code,airport_name,airport_location,
            flight_time_utc,flight_date_utc,seats_available,notes,fetched_from,status,expires_at,created_at,
            requested_flight_date,destination_airport,planned_departure_time,creator_email
        ) VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """,
        (
            data["first_name"].strip().title(),
            data["last_initial"].strip()[:1].upper(),
            raw_phone,
            flight_code,
            airport_code,
            airport_name,
            airport_location,
            "TBD",
            _to_api_flight_date(parsed_flight_date),
            int(data.get("seats_available", 3) or 3),
            str(data.get("notes", "")).strip(),
            "direct",
            "active",
            expires_at,
            created_at,
            flight_date_user,
            destination_airport,
            planned_departure_time,
            creator_email,
        ),
    )

    # Auto-add creator to party_members
    if creator_email:
        try:
            db.execute(
                f"INSERT INTO party_members (carpool_id, user_email, joined_at) VALUES ({p}, {p}, {p})",
                (last_id, creator_email, created_at),
            )
        except Exception:
            pass  # Ignore duplicate

    # Re-read placeholder in case MySQL failed during INSERT and fell back to SQLite
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (last_id,))
    if not rows:
        return jsonify({"error": "Carpool was saved but could not be retrieved"}), 500
    return jsonify({"message": "Carpool created!", "entry": _serialize_entry(rows[0])}), 201


@app.get("/api/airlines/suggest")
def suggest_airlines() -> Any:
    """Return matching airlines for a prefix (instant, no external API)."""
    prefix = request.args.get("q", "").upper().strip()
    if not prefix:
        return jsonify({"results": []})
    matches = [
        {"code": code, "name": name}
        for code, name in AIRLINE_CODES.items()
        if code.startswith(prefix) or name.upper().startswith(prefix)
    ]
    matches.sort(key=lambda x: x["code"])
    return jsonify({"results": matches[:10]})


@app.get("/api/flights/suggest")
def suggest_flights() -> Any:
    query = request.args.get("query", "")
    cleaned = _clean_flight_code(query)
    if len(cleaned) < 2:
        return jsonify({"query": cleaned, "count": 0, "results": []})
    departure_date = (request.args.get("departure_date", "") or request.args.get("flight_date", "")).strip() or None
    # Use broader suggestion for short queries (just airline prefix)
    has_digits = any(c.isdigit() for c in cleaned)
    if has_digits:
        suggestions = _suggest_flights_for_airline(cleaned, departure_date)
    else:
        suggestions = _suggest_flights_for_airline(cleaned, departure_date)
    return jsonify({"query": cleaned, "count": len(suggestions), "results": suggestions})


@app.get("/api/carpools/search")
def search_carpools() -> Any:
    _cleanup_expired_entries()
    flight_code = _clean_flight_code(request.args.get("flight_code", ""))
    airport_code = request.args.get("airport_code", "").upper().strip()
    flight_date_raw = (request.args.get("departure_date", "") or request.args.get("flight_date", "")).strip()
    parsed_search_date = _parse_user_flight_date(flight_date_raw) if flight_date_raw else None
    flight_date = _to_user_flight_date(parsed_search_date) if parsed_search_date else ""

    # Require at least one search field
    if not flight_code and not airport_code and not flight_date_raw:
        return jsonify({"error": "At least 1 search field is required", "count": 0, "results": []}), 400

    current_user = session.get("user_email", "")
    p = db.placeholder

    rows = db.query("SELECT * FROM carpools ORDER BY created_at DESC")

    # Get member counts for all carpools
    member_counts: dict[int, int] = {}
    user_memberships: set[int] = set()
    try:
        all_members = db.query("SELECT carpool_id, user_email FROM party_members")
        for m in all_members:
            cid = m["carpool_id"]
            member_counts[cid] = member_counts.get(cid, 0) + 1
            if m["user_email"] == current_user:
                user_memberships.add(cid)
    except Exception:
        pass

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
        if score > 0:
            public_row = _serialize_entry(entry)
            public_row["match_score"] = score
            public_row["match_reasons"] = reasons
            cid = entry["id"]
            mc = member_counts.get(cid, 0)
            public_row["member_count"] = mc
            public_row["seats_remaining"] = max(0, int(entry.get("seats_available", 3)) - mc)
            public_row["is_member"] = cid in user_memberships
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


@app.post("/api/carpools/<int:carpool_id>/join")
def join_party(carpool_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404

    carpool = rows[0]

    # Check if already a member
    existing = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )
    if existing:
        return jsonify({"error": "Already in this party"}), 409

    # Check seat cap: seats_available includes the creator seat
    members = db.query(
        f"SELECT COUNT(*) as c FROM party_members WHERE carpool_id = {p}",
        (carpool_id,),
    )
    current_count = members[0]["c"] if members else 0
    max_members = int(carpool.get("seats_available", 3))
    if current_count >= max_members:
        return jsonify({"error": "This carpool party is full"}), 409

    # Store phone number if provided
    data = request.get_json(silent=True) or {}
    raw_phone = str(data.get("phone", "")).strip()
    if raw_phone:
        raw_phone = re.sub(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+", " ", raw_phone).strip()
        if PHONE_PATTERN.match(raw_phone):
            try:
                existing_user = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
                if existing_user:
                    db.execute(f"UPDATE users SET phone = {p} WHERE user_email = {p}", (raw_phone, email))
                else:
                    name = session.get("user_name", "")
                    parts = name.strip().split() if name else []
                    first_name = parts[0] if parts else email.split("@")[0]
                    last_initial = parts[-1][0].upper() if len(parts) > 1 and parts[-1] else ""
                    db.execute(
                        f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                        (email, first_name, last_initial, raw_phone, _now_utc().isoformat()),
                    )
            except Exception:
                pass

    db.execute(
        f"INSERT INTO party_members (carpool_id, user_email, joined_at) VALUES ({p}, {p}, {p})",
        (carpool_id, email, _now_utc().isoformat()),
    )
    return jsonify({"ok": True, "message": "Joined the party!"})


@app.post("/api/carpools/<int:carpool_id>/leave")
def leave_party(carpool_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    p = db.placeholder
    # Verify this party exists and the user is currently a member.
    carpool_rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not carpool_rows:
        return jsonify({"error": "Carpool not found"}), 404

    member_rows = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )
    if not member_rows:
        return jsonify({"error": "You are not a member of this party"}), 404

    carpool = carpool_rows[0]
    is_creator = carpool.get("creator_email") == email

    # Remove caller from party first.
    db.execute(
        f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )

    # Non-creators can leave immediately.
    if not is_creator:
        return jsonify({"ok": True, "message": "Left the party."})

    # Creator is leaving: transfer ownership if members remain, otherwise disband.
    remaining_members = db.query(
        f"SELECT user_email, joined_at FROM party_members WHERE carpool_id = {p} ORDER BY joined_at ASC",
        (carpool_id,),
    )

    if not remaining_members:
        # Nobody is left -> auto-disband the party.
        db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))
        return jsonify({"ok": True, "message": "You left and the party was disbanded because no members remained."})

    # Transfer to the earliest-joined remaining member.
    new_owner_email = remaining_members[0]["user_email"]

    # Keep party display data in sync with the new owner if profile exists.
    profile = db.query(
        f"SELECT first_name, last_initial, phone FROM users WHERE user_email = {p}",
        (new_owner_email,),
    )

    if profile:
        first_name = str(profile[0].get("first_name") or "").strip() or new_owner_email.split("@")[0]
        last_initial = str(profile[0].get("last_initial") or "").strip()[:1].upper()
        phone = str(profile[0].get("phone") or "").strip()
        db.execute(
            f"UPDATE carpools SET creator_email = {p}, first_name = {p}, last_initial = {p}, phone = {p} WHERE id = {p}",
            (new_owner_email, first_name, last_initial, phone, carpool_id),
        )
    else:
        db.execute(
            f"UPDATE carpools SET creator_email = {p} WHERE id = {p}",
            (new_owner_email, carpool_id),
        )

    return jsonify({"ok": True, "message": f"Left the party. Ownership transferred to {new_owner_email}."})


@app.get("/api/my-parties")
def my_parties() -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    p = db.placeholder
    memberships = db.query(
        f"SELECT carpool_id FROM party_members WHERE user_email = {p}", (email,)
    )
    if not memberships:
        return jsonify({"parties": []})

    parties = []
    for mem in memberships:
        cid = mem["carpool_id"]
        rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (cid,))
        if not rows:
            continue
        carpool = rows[0]
        members = db.query(
            f"SELECT user_email, joined_at FROM party_members WHERE carpool_id = {p} ORDER BY joined_at",
            (cid,),
        )
        # Enrich members with name and phone from users table
        enriched_members = []
        for m in members:
            member_data = dict(m)
            try:
                user_rows = db.query(f"SELECT first_name, last_initial, phone FROM users WHERE user_email = {p}", (m["user_email"],))
                if user_rows:
                    member_data["first_name"] = user_rows[0]["first_name"]
                    member_data["last_initial"] = user_rows[0]["last_initial"]
                    member_data["phone"] = user_rows[0]["phone"]
                else:
                    member_data["first_name"] = m["user_email"].split("@")[0]
                    member_data["last_initial"] = ""
                    member_data["phone"] = ""
            except Exception:
                member_data["first_name"] = m["user_email"].split("@")[0]
                member_data["last_initial"] = ""
                member_data["phone"] = ""
            enriched_members.append(member_data)
        parties.append({
            "carpool": _serialize_entry(carpool, include_phone=True),
            "members": enriched_members,
            "is_creator": carpool.get("creator_email") == email,
        })

    return jsonify({"parties": parties})


@app.get("/api/user/profile")
def user_profile() -> Any:
    """Get current user's profile (name, phone) for auto-fill."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    try:
        rows = db.query(f"SELECT * FROM users WHERE user_email = {p}", (email,))
        if rows:
            return jsonify({"profile": dict(rows[0])})
    except Exception:
        pass
    return jsonify({"profile": {"user_email": email, "first_name": "", "last_initial": "", "phone": ""}})


@app.post("/api/user/phone")
def update_user_phone() -> Any:
    """Update current user's phone number."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    raw_phone = str(data.get("phone", ""))
    raw_phone = re.sub(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+", " ", raw_phone).strip()
    if not PHONE_PATTERN.match(raw_phone):
        return jsonify({"error": "Phone must be in format +1 (AAA) BBB CCCC"}), 400
    p = db.placeholder
    try:
        existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
        if existing:
            db.execute(f"UPDATE users SET phone = {p} WHERE user_email = {p}", (raw_phone, email))
        else:
            db.execute(
                f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                (email, "", "", raw_phone, _now_utc().isoformat()),
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.post("/api/carpools/<int:carpool_id>/remove-member")
def remove_member(carpool_id: int) -> Any:
    """Allow the party creator to remove a member."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    target_email = str(data.get("email", "")).strip().lower()
    if not target_email:
        return jsonify({"error": "Missing member email"}), 400
    p = db.placeholder
    # Verify caller is the creator
    rows = db.query(f"SELECT creator_email FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    if rows[0]["creator_email"] != email:
        return jsonify({"error": "Only the party creator can remove members"}), 403
    if target_email == email:
        return jsonify({"error": "Cannot remove yourself. Use disband instead."}), 400
    db.execute(
        f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, target_email),
    )
    return jsonify({"ok": True, "message": "Member removed."})


@app.post("/api/carpools/<int:carpool_id>/edit")
def edit_party(carpool_id: int) -> Any:
    """Allow the party creator to edit party details."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    rows = db.query(f"SELECT creator_email FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    if rows[0]["creator_email"] != email:
        return jsonify({"error": "Only the party creator can edit details"}), 403
    data = request.get_json(silent=True) or {}
    updates = []
    params: list[Any] = []
    if "planned_departure_time" in data:
        updates.append(f"planned_departure_time = {p}")
        params.append(str(data["planned_departure_time"]).strip())
    if "notes" in data:
        updates.append(f"notes = {p}")
        params.append(str(data["notes"]).strip())
    if "seats_available" in data:
        try:
            seats = int(data["seats_available"])
            if 1 <= seats <= 7:
                updates.append(f"seats_available = {p}")
                params.append(seats)
        except (ValueError, TypeError):
            pass
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    params.append(carpool_id)
    db.execute(
        f"UPDATE carpools SET {', '.join(updates)} WHERE id = {p}",
        tuple(params),
    )
    return jsonify({"ok": True, "message": "Party updated."})


@app.post("/api/carpools/<int:carpool_id>/disband")
def disband_party(carpool_id: int) -> Any:
    """Allow the party creator to disband the party with a reason."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason", "")).strip()
    if not reason:
        return jsonify({"error": "A reason for disbanding is required"}), 400
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    carpool = rows[0]
    if carpool["creator_email"] != email:
        return jsonify({"error": "Only the party creator can disband the party"}), 403
    # Notify all members (except creator)
    members = db.query(
        f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
        (carpool_id, email),
    )
    now = _now_utc().isoformat()
    creator_name = f"{carpool['first_name']} {carpool['last_initial']}."
    flight = carpool["flight_code"]
    for m in members:
        try:
            db.execute(
                f"INSERT INTO notifications (user_email, message, created_at) VALUES ({p}, {p}, {p})",
                (m["user_email"], f"The carpool for flight {flight} created by {creator_name} has been disbanded. Reason: {reason}", now),
            )
        except Exception:
            pass
    # Delete all party members and the carpool
    db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (carpool_id,))
    db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))
    return jsonify({"ok": True, "message": "Party disbanded."})


@app.get("/api/notifications")
def get_notifications() -> Any:
    """Get unread notifications for current user."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    try:
        rows = db.query(
            f"SELECT * FROM notifications WHERE user_email = {p} AND dismissed = 0 ORDER BY created_at DESC",
            (email,),
        )
        return jsonify({"notifications": [dict(r) for r in rows]})
    except Exception:
        return jsonify({"notifications": []})


@app.post("/api/notifications/<int:notif_id>/dismiss")
def dismiss_notification(notif_id: int) -> Any:
    """Dismiss a notification."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    db.execute(
        f"UPDATE notifications SET dismissed = 1 WHERE id = {p} AND user_email = {p}",
        (notif_id, email),
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.get("/admin/login")
def admin_login_page() -> Any:
    if _require_admin():
        return redirect(url_for("admin_panel"))
    admin_error = request.args.get("error") == "1"
    return render_template("admin_login.html", admin_error=admin_error)


@app.post("/admin/login")
def admin_login() -> Any:
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session.permanent = True
        session["admin_authed"] = True
        session["admin_login_at"] = _now_utc().isoformat()
        return redirect(url_for("admin_panel"))
    return redirect(url_for("admin_login_page", error=1))


@app.get("/admin")
def admin_panel() -> Any:
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    try:
        rows = db.query("SELECT * FROM carpools ORDER BY created_at DESC")
    except Exception:
        rows = []
    total = len(rows)
    unverified = sum(1 for r in rows if r.get("status") == "unverified")
    unique_flights = len({r.get("flight_code", "") for r in rows})
    return render_template("admin.html", entries=rows, total=total,
                           unverified=unverified, unique_flights=unique_flights)


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
    planned_departure_time = request.form.get("planned_departure_time", "").strip()

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
            seats_available = COALESCE(NULLIF({p}, ''), seats_available),
            planned_departure_time = COALESCE(NULLIF({p}, ''), planned_departure_time)
        WHERE id = {p}
        """,
        (first_name, last_initial, phone, notes, flight_code, airport_code, seats, planned_departure_time, entry_id),
    )
    return redirect(url_for("admin_panel"))


@app.route("/admin/logout", methods=["GET", "POST"])
def admin_logout() -> Any:
    session.clear()
    resp = redirect(url_for("admin_login_page"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.after_request
def _add_admin_cache_headers(response: Any) -> Any:
    """Prevent browser from caching admin pages so logout is effective."""
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/health")
def health_check() -> Any:
    """Debug endpoint to check app status on Vercel."""
    actual_engine = "sqlite" if db._mysql_failed else db.engine
    info: dict[str, Any] = {
        "status": "ok",
        "db_engine_configured": DB_ENGINE,
        "db_engine_active": actual_engine,
        "mysql_fallback_active": db._mysql_failed,
        "db_path": DATABASE_PATH if actual_engine == "sqlite" else "(mysql)",
        "serpapi_key_set": bool(SERPAPI_API_KEY),
        "db_initialized": _db_initialized,
    }
    if db._mysql_failed:
        info["mysql_note"] = (
            "MySQL connection failed. The app fell back to SQLite. "
            "Data in /tmp on Vercel will NOT persist between cold starts. "
            "Use a cloud-accessible MySQL provider (TiDB Cloud, PlanetScale, etc.) "
            "instead of InfinityFree for Vercel deployments."
        )
    try:
        count = db.query("SELECT COUNT(*) as c FROM carpools")[0]["c"]
        info["db_count"] = count
    except Exception as e:
        info["db_error"] = str(e)
    return jsonify(info)


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
