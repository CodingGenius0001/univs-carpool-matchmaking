from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import re
import secrets
import sqlite3
import ssl
from threading import Lock
from typing import Any

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
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
SESSION_COOKIE_SECURE_DEFAULT = bool(PUBLIC_APP_URL.startswith("https://") or os.getenv("VERCEL"))
app.config["SESSION_COOKIE_SECURE"] = _truthy_env("SESSION_COOKIE_SECURE", SESSION_COOKIE_SECURE_DEFAULT)


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


MYSQL_CONNECT_TIMEOUT = _positive_int_env("MYSQL_CONNECT_TIMEOUT", 2 if os.getenv("VERCEL") else 5)
MYSQL_READ_TIMEOUT = _positive_int_env("MYSQL_READ_TIMEOUT", 10)
MYSQL_WRITE_TIMEOUT = _positive_int_env("MYSQL_WRITE_TIMEOUT", 10)
MYSQL_RETRY_COOLDOWN_SECONDS = _positive_int_env(
    "MYSQL_RETRY_COOLDOWN_SECONDS",
    600 if os.getenv("VERCEL") else 60,
)

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "campus2air-carpool").strip() or "campus2air-carpool"
FIREBASE_TOKEN_CLOCK_SKEW_SECONDS = _positive_int_env("FIREBASE_TOKEN_CLOCK_SKEW_SECONDS", 300)
ANDROID_APP_PACKAGE = os.getenv("ANDROID_APP_PACKAGE", "com.campus2air.android").strip()
ANDROID_SHA256_CERT_FINGERPRINTS = [
    fp.strip().upper()
    for fp in os.getenv("ANDROID_SHA256_CERT_FINGERPRINTS", "").split(",")
    if fp.strip()
]


@app.template_filter("to_pst")
def to_pst_filter(utc_str: str) -> str:
    """Convert a UTC ISO string to Pacific Standard Time (UTC-8) for display."""
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
        pst = dt.astimezone(timezone(timedelta(hours=-8)))
        return pst.strftime("%m/%d/%Y %I:%M %p PST")
    except Exception:
        return str(utc_str)[:16]

ADMIN_USERNAME = "admin"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
_admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
_admin_password_plain = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_LOGIN_ENABLED = bool(_admin_password_hash or _admin_password_plain)
if _admin_password_hash:
    ADMIN_PASSWORD_HASH = _admin_password_hash
elif _admin_password_plain:
    ADMIN_PASSWORD_HASH = generate_password_hash(_admin_password_plain)
else:
    # Keep the admin panel disabled until credentials are configured explicitly.
    ADMIN_PASSWORD_HASH = generate_password_hash("admin-login-disabled")
FLIGHT_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$")
PHONE_PATTERN = re.compile(r"^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$")
NAME_PATTERN = re.compile(r"^[A-Za-z \-']+$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


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


_firebase_request_adapter: Any | None = None


def _verify_firebase_id_token(id_token: str) -> dict[str, Any]:
    global _firebase_request_adapter

    try:
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:
        raise RuntimeError(
            "google-auth is required to verify Firebase sessions. "
            "Install dependencies from requirements.txt before running the app."
        ) from exc
    try:
        from google.auth.transport.requests import Request as GoogleRequest
    except ImportError as exc:
        raise RuntimeError(
            "The requests package is required to verify Firebase sessions. "
            "Install dependencies from requirements.txt before running the app."
        ) from exc

    last_error: Exception | None = None
    for _ in range(2):
        if _firebase_request_adapter is None:
            _firebase_request_adapter = GoogleRequest()
        try:
            claims = google_id_token.verify_firebase_token(
                id_token,
                _firebase_request_adapter,
                audience=FIREBASE_PROJECT_ID,
                clock_skew_in_seconds=FIREBASE_TOKEN_CLOCK_SKEW_SECONDS,
            )
            break
        except Exception as exc:
            last_error = exc
            _firebase_request_adapter = GoogleRequest()
    else:
        raise last_error or ValueError("Invalid Firebase ID token")

    if not claims:
        raise ValueError("Invalid Firebase ID token")

    expected_issuer = f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}"
    if claims.get("aud") != FIREBASE_PROJECT_ID or claims.get("iss") != expected_issuer:
        raise ValueError("Firebase token audience or issuer mismatch")

    return claims


def _get_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _request_csrf_token() -> str:
    header_token = request.headers.get("X-CSRF-Token", "").strip()
    if header_token:
        return header_token

    form_token = request.form.get("csrf_token", "").strip()
    if form_token:
        return form_token

    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return str(payload.get("csrf_token", "")).strip()

    return ""


@app.context_processor
def inject_template_helpers() -> dict[str, Any]:
    return {"csrf_token": _get_csrf_token}


@app.before_request
def protect_csrf() -> Any:
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None

    expected = session.get("csrf_token", "")
    provided = _request_csrf_token()
    if expected and provided and hmac.compare_digest(expected, provided):
        return None

    if request.path.startswith("/api/") or request.path == "/auth/firebase-callback":
        return jsonify({"error": "Invalid CSRF token"}), 400
    return "Invalid CSRF token", 400


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
        self._schema_ready = False
        self._schema_lock = Lock()

    def _activate_sqlite_fallback(self, reason: str = "") -> None:
        """Switch to SQLite fallback and retry MySQL after a cooldown."""
        import time
        if not self._mysql_failed:
            app.logger.warning(f"Switching to SQLite fallback. {reason}")
        self._mysql_failed = True
        self._mysql_failed_at = time.time()
        self.placeholder = "?"
        self._schema_ready = False
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
        return (time.time() - self._mysql_failed_at) > MYSQL_RETRY_COOLDOWN_SECONDS

    def _reset_mysql_retry_state(self) -> None:
        app.logger.info("Retrying MySQL connection after cooldown.")
        self._mysql_failed = False
        self._mysql_failed_at = None
        self.placeholder = "%s"
        self._schema_ready = False

    def _ensure_sqlite_dir(self) -> None:
        if DATABASE_PATH in ("", ":memory:"):
            return
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    def ensure_ready(self) -> None:
        with self._schema_lock:
            if self.engine == "mysql" and self._should_retry_mysql():
                self._reset_mysql_retry_state()
            if self._schema_ready:
                return
            if self.engine == "sqlite" or self._mysql_failed:
                self._ensure_sqlite_dir()
            self.init_schema()
            self.ensure_columns()
            self._schema_ready = True

    def _normalized_sql(self, sql: str, conn: Any | None = None) -> str:
        if isinstance(conn, sqlite3.Connection):
            return sql.replace("%s", "?")
        if self.engine == "sqlite" or self._mysql_failed:
            return sql.replace("%s", "?")
        return sql

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
                    connect_timeout=MYSQL_CONNECT_TIMEOUT,
                    read_timeout=MYSQL_READ_TIMEOUT,
                    write_timeout=MYSQL_WRITE_TIMEOUT,
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
        self.ensure_ready()
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(self._normalized_sql(sql, conn), params)
            rows = cur.fetchall()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL query failed: {exc}")
                self.ensure_ready()
                conn = self.get_conn()
                cur = conn.cursor()
                cur.execute(self._normalized_sql(sql, conn), params)
                rows = cur.fetchall()
                cur.close()
            else:
                raise
        if self._mysql_failed or self.engine == "sqlite":
            return [dict(r) for r in rows]
        return list(rows)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.ensure_ready()
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(self._normalized_sql(sql, conn), params)
            last_id = getattr(cur, "lastrowid", 0)
            conn.commit()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL execute failed: {exc}")
                self.ensure_ready()
                conn = self.get_conn()
                cur = conn.cursor()
                cur.execute(self._normalized_sql(sql, conn), params)
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
                    connect_timeout=MYSQL_CONNECT_TIMEOUT,
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


def notify_user(email: str, message: str) -> None:
    p = db.placeholder
    db.execute(
        f"INSERT INTO notifications (user_email, message, created_at) VALUES ({p}, {p}, {p})",
        (email, message, _now_utc().isoformat()),
    )


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




def _resolve_airport(code: str) -> tuple[str, str]:
    airport = AIRPORT_CODE_MAP.get(code)
    if airport:
        return airport["name"], airport["location"]
    return f"Airport {code}", "Unknown location"


def _serialize_entry(row: dict[str, Any], view: str = "public") -> dict[str, Any]:
    data = dict(row)
    hidden_fields = {"expires_at", "created_at", "fetched_from", "status"}
    if view == "public":
        hidden_fields.update({"phone", "creator_email", "notes"})
    for field in hidden_fields:
        data.pop(field, None)
    return data


def _parse_seat_count(value: Any) -> int | None:
    try:
        seats = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seats if 1 <= seats <= 7 else None


def _normalize_departure_time(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not TIME_PATTERN.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def _derive_user_name_parts(raw_name: str, fallback_email: str = "") -> tuple[str, str]:
    parts = raw_name.strip().split() if raw_name else []
    first_name = parts[0] if parts else (fallback_email.split("@")[0] if fallback_email else "")
    last_initial = parts[-1][0].upper() if len(parts) > 1 and parts[-1] else ""
    first_name = re.sub(r"[^A-Za-z \-']", "", first_name).strip().title()
    if not first_name and fallback_email:
        first_name = re.sub(r"[^A-Za-z0-9_\-']", "", fallback_email.split("@")[0]).strip()
    last_initial = re.sub(r"[^A-Za-z]", "", last_initial).upper()[:1]
    return first_name, last_initial


def _owner_profile_fields(user_email: str) -> tuple[str, str, str]:
    p = db.placeholder
    profile = db.query(
        f"SELECT first_name, last_initial, phone FROM users WHERE user_email = {p}",
        (user_email,),
    )
    if profile:
        first_name = str(profile[0].get("first_name") or "").strip()
        last_initial = str(profile[0].get("last_initial") or "").strip()[:1].upper()
        phone = str(profile[0].get("phone") or "").strip()
        if first_name:
            return first_name, last_initial, phone
    fallback_first_name, fallback_last_initial = _derive_user_name_parts("", user_email)
    return fallback_first_name, fallback_last_initial, ""


def _delete_carpool_party(carpool_id: int) -> None:
    p = db.placeholder
    db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (carpool_id,))
    db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))


def _clear_all_data() -> None:
    db.execute("DELETE FROM party_members")
    db.execute("DELETE FROM notifications")
    db.execute("DELETE FROM users")
    db.execute("DELETE FROM carpools")


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


@app.errorhandler(Exception)
def handle_exception(e: Exception) -> Any:
    """Return JSON errors for API routes, HTML for pages."""
    tb = traceback.format_exc()
    app.logger.error(f"Unhandled exception: {e}\n{tb}")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Server error"}), 500
    return "<h1>Internal Server Error</h1><p>Please try again later.</p>", 500


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


@app.get("/manifest.webmanifest")
def web_manifest() -> Any:
    manifest = {
        "id": "/",
        "name": "Campus2Air",
        "short_name": "Campus2Air",
        "description": "University carpool matching for shared airport rides.",
        "start_url": "/login?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#edf4ff",
        "theme_color": "#2f6fdd",
        "orientation": "portrait",
        "icons": [
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
        "categories": ["travel", "education", "navigation"],
        "prefer_related_applications": False,
    }
    response = app.response_class(
        response=json.dumps(manifest, separators=(",", ":")),
        mimetype="application/manifest+json",
    )
    return response


@app.get("/service-worker.js")
def service_worker() -> Any:
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    return send_from_directory(static_dir, "service-worker.js", mimetype="application/javascript")


@app.get("/offline")
def offline_page() -> Any:
    return render_template("offline.html", **_user_context())


@app.get("/.well-known/assetlinks.json")
def assetlinks() -> Any:
    if not ANDROID_SHA256_CERT_FINGERPRINTS:
        return jsonify([])

    return jsonify(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": ANDROID_APP_PACKAGE,
                    "sha256_cert_fingerprints": ANDROID_SHA256_CERT_FINGERPRINTS,
                },
            }
        ]
    )


def _require_user_login() -> bool:
    """Check if a regular user is logged in via Firebase."""
    return bool(session.get("user_email"))


def _user_context() -> dict[str, Any]:
    """Build common template context for logged-in pages."""
    email = session.get("user_email", "")
    raw_name = session.get("user_name", "")
    # Build short display name e.g. "Keshav P" from Google display name
    parts = raw_name.strip().split() if raw_name else []
    if len(parts) >= 2:
        display_name = f"{parts[0]} {parts[-1][0].upper()}"
    elif parts:
        display_name = parts[0]
    else:
        display_name = ""
    has_party = False
    if email:
        try:
            p = db.placeholder
            rows = db.query(f"SELECT COUNT(*) as c FROM party_members WHERE user_email = {p}", (email,))
            has_party = rows[0]["c"] > 0 if rows else False
        except Exception:
            pass
    return {"user_email": email, "has_party": has_party, "display_name": display_name}


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
    """Receive a Firebase ID token from the client, verify it, and set session."""
    data = request.get_json(silent=True) or {}
    firebase_id_token = str(data.get("idToken", "")).strip()

    if not firebase_id_token:
        return jsonify({"error": "Missing Firebase ID token"}), 400

    try:
        claims = _verify_firebase_id_token(firebase_id_token)
    except ValueError as exc:
        app.logger.warning("Firebase token verification failed: %s", exc)
        return jsonify({"error": "Invalid authentication token"}), 401
    except Exception as exc:
        app.logger.error("Firebase token verification unavailable: %s", exc)
        return jsonify({"error": "Authentication service unavailable"}), 503

    email = str(claims.get("email", "")).strip().lower()
    name = str(claims.get("name", "")).strip()
    uid = str(claims.get("user_id") or claims.get("uid") or claims.get("sub") or "").strip()
    email_verified = bool(claims.get("email_verified"))

    if not email or not uid:
        return jsonify({"error": "Incomplete authentication claims"}), 400
    if not email_verified:
        return jsonify({"error": "Email must be verified"}), 403
    if not email.endswith("@ucr.edu"):
        return jsonify({"error": "Only @ucr.edu accounts are allowed"}), 403

    session.clear()
    session.permanent = True
    session["user_email"] = email
    session["user_name"] = name
    session["user_uid"] = uid
    _get_csrf_token()

    # Store/update user profile from Google display name
    first_name, last_initial = _derive_user_name_parts(name, email)
    p = db.placeholder
    try:
        existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
        if not existing:
            db.execute(
                f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                (email, first_name, last_initial, "", _now_utc().isoformat()),
            )
        else:
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
        return jsonify({"error": "Server error"}), 500


def _create_carpool_inner() -> Any:
    if not _require_user_login():
        return jsonify({"error": "Login required"}), 401

    _cleanup_expired_entries()
    data = request.get_json(silent=True) or request.form.to_dict()

    required = ["phone", "flight_code", "airport_code"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if not str(data.get("departure_date") or data.get("flight_date") or "").strip():
        missing.append("departure_date")
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    creator_email = session.get("user_email", "")
    first_name, last_initial = _derive_user_name_parts(session.get("user_name", ""), creator_email)
    if not first_name:
        return jsonify({"error": "Could not determine your name from your login session"}), 400

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

    seats_available = _parse_seat_count(data.get("seats_available", 4))
    if seats_available is None:
        return jsonify({"error": "seats_available must be a whole number between 1 and 7"}), 400

    planned_departure_time = _normalize_departure_time(data.get("planned_departure_time", ""))
    if planned_departure_time is None:
        return jsonify({"error": "planned_departure_time must use HH:MM format"}), 400

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()

    # Expire at 11:59 PM UTC the day AFTER the departure date.
    # +1 day buffer prevents users in UTC-offset timezones (e.g. California, UTC-8)
    # from having their carpool instantly cleaned up when their local "today"
    # is already "yesterday" in UTC.
    expires_at = (parsed_flight_date + timedelta(days=1)).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).isoformat()

    destination_airport = str(data.get("destination_airport", "")).strip().upper()[:3]

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
            first_name,
            last_initial,
            raw_phone,
            flight_code,
            airport_code,
            airport_name,
            airport_location,
            "TBD",
            _to_api_flight_date(parsed_flight_date),
            seats_available,
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

    if creator_email:
        try:
            notify_user(creator_email, f"Your carpool for {flight_code} on {flight_date_user} has been created! Share your carpool to find ride partners.")
        except Exception:
            pass


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



@app.get("/api/carpools/search")
def search_carpools() -> Any:
    if not _require_user_login():
        return jsonify({"error": "Login required", "count": 0, "results": []}), 401

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

    rows = db.query(f"SELECT * FROM carpools WHERE status = {p} ORDER BY created_at DESC", ("active",))

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
        matched_fields = 0
        reasons: list[str] = []
        if flight_code and entry["flight_code"] == flight_code:
            matched_fields += 1
            reasons.append("Exact flight code match")
        if airport_code and entry["airport_code"] == airport_code:
            matched_fields += 1
            reasons.append("Same airport code")
        if flight_date and entry.get("requested_flight_date") == flight_date:
            matched_fields += 1
            reasons.append("Same requested flight date")
        if matched_fields > 0:
            score = round((matched_fields / 3) * 100)
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
    email = session.get("user_email", "")
    if not email and not _require_admin():
        return jsonify({"error": "Login required"}), 401

    _cleanup_expired_entries()
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (entry_id,))
    if not rows:
        return jsonify({"error": "Not found"}), 404
    if not _require_admin():
        member_rows = db.query(
            f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
            (entry_id, email),
        )
        if not member_rows:
            return jsonify({"error": "Forbidden"}), 403
    return jsonify({"entry": _serialize_entry(rows[0], view="member")})


@app.post("/api/carpools/<int:carpool_id>/join")
def join_party(carpool_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    # Store phone number if provided
    p = db.placeholder
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
                    first_name, last_initial = _derive_user_name_parts(session.get("user_name", ""), email)
                    db.execute(
                        f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                        (email, first_name, last_initial, raw_phone, _now_utc().isoformat()),
                    )
            except Exception:
                pass

    db.ensure_ready()
    conn = db.get_conn()
    cur = conn.cursor()
    carpool: dict[str, Any] | None = None
    existing_members: list[dict[str, Any]] = []

    try:
        if isinstance(conn, sqlite3.Connection):
            conn.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT * FROM carpools WHERE id = ?", (carpool_id,))
        else:
            conn.begin()
            cur.execute("SELECT * FROM carpools WHERE id = %s FOR UPDATE", (carpool_id,))

        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({"error": "Carpool not found"}), 404

        carpool = dict(row)
        if carpool.get("status") != "active":
            conn.rollback()
            return jsonify({"error": "This carpool is no longer active"}), 409

        membership_sql = db._normalized_sql(
            f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
            conn,
        )
        cur.execute(membership_sql, (carpool_id, email))
        if cur.fetchone():
            conn.rollback()
            return jsonify({"error": "Already in this carpool"}), 409

        existing_members_sql = db._normalized_sql(
            f"SELECT user_email FROM party_members WHERE carpool_id = {p}",
            conn,
        )
        cur.execute(existing_members_sql, (carpool_id,))
        existing_members = [dict(member) for member in cur.fetchall()]

        max_members = _parse_seat_count(carpool.get("seats_available", 3)) or 3
        if len(existing_members) >= max_members:
            conn.rollback()
            return jsonify({"error": "This carpool is full"}), 409

        insert_sql = db._normalized_sql(
            f"INSERT INTO party_members (carpool_id, user_email, joined_at) VALUES ({p}, {p}, {p})",
            conn,
        )
        cur.execute(insert_sql, (carpool_id, email, _now_utc().isoformat()))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()

    # Notify creator and all existing members that someone joined
    joiner_name = session.get("user_name", email.split("@")[0])
    flight_code = carpool.get("flight_code", "") if carpool else ""
    flight_date = carpool.get("requested_flight_date", "") if carpool else ""
    msg = f"{joiner_name} joined your carpool for {flight_code} on {flight_date}."
    creator_email = carpool.get("creator_email", "") if carpool else ""
    notified = {email}  # don't notify the joiner themselves
    try:
        for member in existing_members:
            mem_email = member["user_email"]
            if mem_email not in notified:
                notify_user(mem_email, msg)
                notified.add(mem_email)
        if creator_email and creator_email not in notified:
            notify_user(creator_email, msg)
    except Exception:
        pass

    return jsonify({"ok": True, "message": "Joined the carpool!"})


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
        return jsonify({"error": "You are not a member of this carpool"}), 404

    carpool = carpool_rows[0]
    is_creator = carpool.get("creator_email") == email

    # Remove caller from party first.
    db.execute(
        f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )

    # Non-creators can leave immediately.
    if not is_creator:
        creator_email = carpool.get("creator_email", "")
        flight_code = carpool.get("flight_code", "")
        flight_date = carpool.get("requested_flight_date", "")
        leaver_name = session.get("user_name", email.split("@")[0])
        if creator_email:
            try:
                notify_user(creator_email, f"{leaver_name} left your carpool for {flight_code} on {flight_date}.")
            except Exception:
                pass
        return jsonify({"ok": True, "message": "Left the carpool."})

    # Creator is leaving: transfer ownership if members remain, otherwise disband.
    remaining_members = db.query(
        f"SELECT user_email, joined_at FROM party_members WHERE carpool_id = {p} ORDER BY joined_at ASC",
        (carpool_id,),
    )

    if not remaining_members:
        # Nobody is left -> auto-disband the party.
        _delete_carpool_party(carpool_id)
        return jsonify({"ok": True, "message": "You left and the carpool was disbanded because no members remained."})

    # Transfer to the earliest-joined remaining member.
    new_owner_email = remaining_members[0]["user_email"]
    first_name, last_initial, phone = _owner_profile_fields(new_owner_email)
    db.execute(
        f"UPDATE carpools SET creator_email = {p}, first_name = {p}, last_initial = {p}, phone = {p} WHERE id = {p}",
        (new_owner_email, first_name, last_initial, phone, carpool_id),
    )

    flight_code = carpool.get("flight_code", "")
    flight_date = carpool.get("requested_flight_date", "")
    try:
        notify_user(new_owner_email, f"You are now the organizer of the carpool for {flight_code} on {flight_date}. The previous creator left.")
    except Exception:
        pass
    return jsonify({"ok": True, "message": f"Left the carpool. Ownership transferred to {new_owner_email}."})


@app.post("/api/carpools/<int:carpool_id>/transfer-and-leave")
def transfer_and_leave(carpool_id: int) -> Any:
    """Creator picks a specific member to receive ownership, then leaves."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    new_owner_email = str(data.get("new_owner_email", "")).strip()
    if not new_owner_email:
        return jsonify({"error": "new_owner_email is required"}), 400
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    if rows[0].get("creator_email") != email:
        return jsonify({"error": "Only the creator can transfer ownership"}), 403
    member_check = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, new_owner_email),
    )
    if not member_check:
        return jsonify({"error": "New owner must be a current carpool member"}), 400
    fn, li, ph = _owner_profile_fields(new_owner_email)
    db.execute(
        f"UPDATE carpools SET creator_email = {p}, first_name = {p}, last_initial = {p}, phone = {p} WHERE id = {p}",
        (new_owner_email, fn, li, ph, carpool_id),
    )
    db.execute(
        f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )
    carpool_row = rows[0]
    flight_code = carpool_row.get("flight_code", "")
    flight_date = carpool_row.get("requested_flight_date", "")
    try:
        notify_user(new_owner_email, f"You are now the organizer of the carpool for {flight_code} on {flight_date}.")
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Ownership transferred. You have left the carpool."})


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
            "carpool": _serialize_entry(carpool, view="member"),
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
            first_name, last_initial = _derive_user_name_parts(session.get("user_name", ""), email)
            db.execute(
                f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                (email, first_name, last_initial, raw_phone, _now_utc().isoformat()),
            )
    except Exception:
        return jsonify({"error": "Could not update your phone number"}), 500
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
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    if rows[0]["creator_email"] != email:
        return jsonify({"error": "Only the carpool creator can remove members"}), 403
    if target_email == email:
        return jsonify({"error": "Cannot remove yourself. Use disband instead."}), 400
    membership_rows = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, target_email),
    )
    if not membership_rows:
        return jsonify({"error": "That member is not in this carpool"}), 404
    db.execute(
        f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, target_email),
    )
    carpool = rows[0]
    creator_name = f"{carpool.get('first_name', '')} {carpool.get('last_initial', '')}."
    flight_code = carpool.get("flight_code", "")
    flight_date = carpool.get("requested_flight_date", "")
    try:
        notify_user(target_email, f"You were removed from the carpool for {flight_code} on {flight_date} by {creator_name}.")
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Member removed."})


@app.post("/api/carpools/<int:carpool_id>/edit")
def edit_party(carpool_id: int) -> Any:
    """Allow the carpool creator to edit carpool details."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    if rows[0]["creator_email"] != email:
        return jsonify({"error": "Only the carpool creator can edit details"}), 403
    data = request.get_json(silent=True) or {}
    updates = []
    params: list[Any] = []
    changed: list[str] = []
    if "planned_departure_time" in data:
        normalized_time = _normalize_departure_time(data["planned_departure_time"])
        if normalized_time is None:
            return jsonify({"error": "planned_departure_time must use HH:MM format"}), 400
        updates.append(f"planned_departure_time = {p}")
        params.append(normalized_time)
        changed.append("departure time")
    if "notes" in data:
        updates.append(f"notes = {p}")
        params.append(str(data["notes"]).strip())
        changed.append("notes")
    if "seats_available" in data:
        seats = _parse_seat_count(data["seats_available"])
        if seats is None:
            return jsonify({"error": "seats_available must be a whole number between 1 and 7"}), 400
        member_rows = db.query(
            f"SELECT COUNT(*) as c FROM party_members WHERE carpool_id = {p}",
            (carpool_id,),
        )
        current_members = int(member_rows[0]["c"]) if member_rows else 0
        if seats < current_members:
            return jsonify({"error": "seats_available cannot be lower than the current member count"}), 400
        updates.append(f"seats_available = {p}")
        params.append(seats)
        changed.append("seat count")
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    params.append(carpool_id)
    db.execute(
        f"UPDATE carpools SET {', '.join(updates)} WHERE id = {p}",
        tuple(params),
    )
    # Notify all members except the creator
    if changed:
        carpool = rows[0]
        flight_code = carpool.get("flight_code", "")
        flight_date = carpool.get("requested_flight_date", "")
        changed_str = ", ".join(changed)
        members = db.query(
            f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
            (carpool_id, email),
        )
        for m in members:
            try:
                notify_user(m["user_email"], f"The carpool for {flight_code} on {flight_date} has been updated ({changed_str}) by the organizer.")
            except Exception:
                pass
    return jsonify({"ok": True, "message": "Carpool updated."})


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
        return jsonify({"error": "Only the carpool creator can disband the carpool"}), 403
    # Notify all members (except creator)
    members = db.query(
        f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
        (carpool_id, email),
    )
    creator_name = f"{carpool['first_name']} {carpool['last_initial']}."
    flight = carpool["flight_code"]
    for m in members:
        try:
            notify_user(m["user_email"], f"The carpool for flight {flight} created by {creator_name} has been disbanded. Reason: {reason}")
        except Exception:
            pass
    _delete_carpool_party(carpool_id)
    return jsonify({"ok": True, "message": "Carpool disbanded."})


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
    return render_template(
        "admin_login.html",
        admin_error=admin_error,
        admin_login_enabled=ADMIN_LOGIN_ENABLED,
    )


@app.post("/admin/login")
def admin_login() -> Any:
    if not ADMIN_LOGIN_ENABLED:
        return redirect(url_for("admin_login_page"))
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
    _clear_all_data()
    return redirect(url_for("admin_panel"))


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    _delete_carpool_party(entry_id)
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
    seats_raw = request.form.get("seats_available", "").strip()
    planned_departure_time_raw = request.form.get("planned_departure_time", "").strip()

    if seats_raw:
        seats = _parse_seat_count(seats_raw)
        if seats is None:
            return "Invalid seat count", 400
        p = db.placeholder
        member_rows = db.query(
            f"SELECT COUNT(*) as c FROM party_members WHERE carpool_id = {p}",
            (entry_id,),
        )
        current_members = int(member_rows[0]["c"]) if member_rows else 0
        if seats < current_members:
            return "Seat count cannot be lower than the current member count", 400
        seats_value = str(seats)
    else:
        seats_value = ""

    planned_departure_time = _normalize_departure_time(planned_departure_time_raw)
    if planned_departure_time is None:
        return "Invalid departure time", 400

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
        (first_name, last_initial, phone, notes, flight_code, airport_code, seats_value, planned_departure_time, entry_id),
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
    """Prevent caching where stale state causes problems."""
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.path == "/service-worker.js":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
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
        "db_initialized": db._schema_ready,
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

if __name__ == "__main__":
    try:
        with app.app_context():
            db.ensure_ready()
    except Exception as exc:
        print(f"[startup] database initialization failed: {exc}")
    app.run(host="0.0.0.0", port=8000, debug=True)
