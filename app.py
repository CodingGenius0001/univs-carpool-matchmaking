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
from zoneinfo import ZoneInfo

import traceback

# Load .env file for local development (silently ignored if not present or
# if python-dotenv is not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
_configured_secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
if not _configured_secret_key or _configured_secret_key == "dev-only-change-me":
    # Never fall back to a well-known key: anyone who knows it could forge a
    # session for any user. A random per-process key is safe, but sessions will
    # not survive restarts or be shared across serverless instances, so set
    # FLASK_SECRET_KEY in every deployed environment.
    _configured_secret_key = secrets.token_urlsafe(32)
    if os.getenv("VERCEL"):
        print("[security] FLASK_SECRET_KEY is not set; using a random per-process key. Set it in Vercel.")
app.secret_key = _configured_secret_key
# Students stay signed in for 30 days after their last visit (the cookie is
# refreshed on every request). Admin access has its own 30-minute limit in
# _require_admin().
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# Student and admin logins share one session cookie; each login/logout only
# touches its own keys so signing in or out of one never ends the other (and
# never changes the CSRF token that open tabs are using).
USER_SESSION_KEYS = ("user_email", "user_name", "user_uid", "post_login_next")
ADMIN_SESSION_KEYS = ("admin_authed", "admin_login_at")
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
    """Convert a UTC ISO string to Pacific time (PST/PDT) for display."""
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(utc_str).replace("Z", "+00:00"))
        pacific = dt.astimezone(ZoneInfo("America/Los_Angeles"))
        return pacific.strftime("%m/%d/%Y %I:%M %p %Z")
    except Exception:
        return str(utc_str)[:16]

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
AIRPORT_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

# Terms of Service & EULA (templates/_terms_body.html). Bump the version for
# any material change: everyone is asked to agree again at their next sign-in,
# and the version each user accepted is recorded in the users table.
TERMS_VERSION = "4.2"
TERMS_EFFECTIVE = "October 13, 2026"
# Where legal notices, opt-outs and privacy requests go (shown in the Terms
# and Privacy Policy). Never a public repository.
LEGAL_CONTACT_EMAIL = os.getenv("LEGAL_CONTACT_EMAIL", "").strip() or "legal-campus2air@proton.me"
# Google Form linked from the ☰ menu ("Give Feedback").
FEEDBACK_URL = "https://forms.gle/Lv4HinTodaqzJawm8"
# Which way the ride goes. Carpools created before this existed are campus -> airport.
DIRECTIONS = ("to_airport", "from_airport")
DEFAULT_DIRECTION = "to_airport"
SHARE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
INVITE_PATH_PATTERN = re.compile(r"^/c/[A-Za-z0-9_-]{8,32}$")

MAX_NOTES_LENGTH = 500
MAX_REASON_LENGTH = 300
MAX_DAYS_AHEAD = 366
# Anti-abuse: how many carpools (created by someone else) a user may join in a
# rolling window. Leaving does not refund a join, so join/leave loops are capped.
NOTIFICATION_RETENTION = timedelta(days=90)
INACTIVE_ACCOUNT_RETENTION = timedelta(days=365)
SUSPENSION_RETENTION = timedelta(days=3 * 365)
MAX_BAN_REASON_LENGTH = 300
JOIN_LIMIT_COUNT = 2
JOIN_LIMIT_WINDOW = timedelta(hours=24)
# Search treats leave times within this many minutes of each other as a match.
LEAVE_TIME_WINDOW_MINUTES = 120


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
        # Without this the cookie dies when the browser closes (phones do
        # that in the background), breaking an open login page's token.
        session.permanent = True
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
    return {
        "csrf_token": _get_csrf_token,
        "terms_version": TERMS_VERSION,
        "terms_effective": TERMS_EFFECTIVE,
        "legal_contact_email": LEGAL_CONTACT_EMAIL,
        "feedback_url": FEEDBACK_URL,
    }


BANNED_MESSAGE = (
    "This account has been suspended from Campus2Air. "
    f"If you think this is a mistake, email {LEGAL_CONTACT_EMAIL}."
)


@app.before_request
def block_banned_users() -> Any:
    """A banned student's existing session stops working immediately."""
    email = session.get("user_email")
    if not email or request.path.startswith("/static/"):
        return None
    try:
        banned = _is_banned(email)
    except Exception:
        return None
    if not banned:
        return None
    for key in USER_SESSION_KEYS:
        session.pop(key, None)
    if request.path.startswith("/api/"):
        return jsonify({"error": BANNED_MESSAGE}), 403
    return redirect(url_for("login_page", error="suspended"))


@app.before_request
def protect_csrf() -> Any:
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None

    expected = session.get("csrf_token", "")
    provided = _request_csrf_token()
    if expected and provided and hmac.compare_digest(expected, provided):
        return None

    # A stale logout form (session expired, or already logged out in another
    # tab) shouldn't show a CSRF error. Redirecting without logging out is
    # safe: nothing changes, so a forged request gains nothing.
    if request.path == "/auth/logout":
        return redirect(url_for("login_page"))
    if request.path == "/admin/logout":
        return redirect(url_for("admin_login_page"))

    # Usually an old tab: the session expired or changed since the page
    # loaded. Say so plainly instead of "Invalid CSRF token".
    message = "Your session changed since this page was opened. Refresh the page and try again."
    if request.path.startswith("/api/") or request.path == "/auth/firebase-callback":
        return jsonify({"error": message, "session_expired": True}), 400
    if request.path.startswith("/admin"):
        return redirect(url_for("admin_panel" if _require_admin() else "admin_login_page"))
    return redirect(url_for("login_page"))


def _resolve_database_path() -> str:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "/tmp/carpool.db"
    return "carpool.db"


DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()
# When DB_ENGINE=mysql and MySQL is unreachable, should the app quietly switch
# to a local SQLite file? Fine for local development, but on Vercel that file
# lives in /tmp: it is per-instance and wiped on cold starts, so writes are
# silently lost. Off by default on Vercel (requests fail with 503 instead).
ALLOW_SQLITE_FALLBACK = _truthy_env("ALLOW_SQLITE_FALLBACK", not os.getenv("VERCEL"))
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


class DatabaseUnavailable(Exception):
    """MySQL is unreachable and SQLite fallback is disabled."""


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

    def _handle_mysql_failure(self, reason: str, exc: Exception | None = None) -> None:
        """Fall back to SQLite if allowed; otherwise fail this request loudly.

        Without fallback nothing is remembered across requests, so the next
        request tries MySQL again. Within one request we fail fast so a
        request doesn't wait out several connect timeouts.
        """
        if ALLOW_SQLITE_FALLBACK:
            self._activate_sqlite_fallback(reason)
            return
        app.logger.error(f"Database unavailable (SQLite fallback disabled): {reason}")
        old = g.pop("db", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        g.db_unavailable = True
        raise DatabaseUnavailable(reason) from exc

    def _raise_if_unavailable(self) -> None:
        if g.get("db_unavailable"):
            raise DatabaseUnavailable("MySQL connection already failed during this request")

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
        self._raise_if_unavailable()
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
        self._raise_if_unavailable()

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
                self._handle_mysql_failure(
                    f"MySQL connection failed to {mysql_host}: {exc}. "
                    "If using InfinityFree or similar shared hosting, their MySQL "
                    "only accepts connections from their own servers — not from "
                    "Vercel/external services.",
                    exc,
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
                self._handle_mysql_failure(f"MySQL query failed: {exc}", exc)
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
                self._handle_mysql_failure(f"MySQL execute failed: {exc}", exc)
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
                # Raises DatabaseUnavailable unless fallback is allowed.
                self._handle_mysql_failure(f"MySQL init_schema failed ({mysql_host}): {exc}", exc)
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

        # Append-only log of joins, used for the rolling join limit. Rows are
        # kept after a user leaves so leaving does not reset the limit.
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS join_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_email VARCHAR(255) NOT NULL,
                    carpool_id INT NOT NULL,
                    joined_at VARCHAR(64) NOT NULL,
                    INDEX idx_join_log_user (user_email, joined_at)
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS join_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    carpool_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_join_log_user ON join_log (user_email, joined_at)")

        # Flight numbers of a carpool's riders, collected when they join. Stored
        # per carpool only (no user column) so a flight is never tied to a person.
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carpool_flights (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    carpool_id INT NOT NULL,
                    flight_code VARCHAR(16) NOT NULL,
                    added_at VARCHAR(64) NOT NULL,
                    UNIQUE KEY uq_carpool_flight (carpool_id, flight_code)
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carpool_flights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    carpool_id INTEGER NOT NULL,
                    flight_code TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    UNIQUE (carpool_id, flight_code)
                )
                """
            )

        # Suspended accounts (admin-only tool). Kept after a user's data is
        # deleted so a suspension can't be undone by a deletion request.
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_email VARCHAR(255) PRIMARY KEY,
                    reason TEXT,
                    banned_at VARCHAR(64) NOT NULL
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_email TEXT PRIMARY KEY,
                    reason TEXT,
                    banned_at TEXT NOT NULL
                )
                """
            )

        # Analytics. Carpools are deleted after departure, so a small record of
        # each one is kept here for the admin dashboard (no phones or notes).
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carpool_history (
                    carpool_id INT PRIMARY KEY,
                    flight_code VARCHAR(16) NOT NULL DEFAULT '',
                    airport_code VARCHAR(3) NOT NULL DEFAULT '',
                    flight_date VARCHAR(16) NOT NULL DEFAULT '',
                    seats INT NOT NULL DEFAULT 0,
                    peak_members INT NOT NULL DEFAULT 1,
                    created_at VARCHAR(64) NOT NULL DEFAULT '',
                    closed_at VARCHAR(64) NOT NULL DEFAULT '',
                    outcome VARCHAR(32) NOT NULL DEFAULT ''
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    event VARCHAR(32) NOT NULL,
                    user_email VARCHAR(255) NOT NULL DEFAULT '',
                    carpool_id INT NOT NULL DEFAULT 0,
                    detail VARCHAR(255) NOT NULL DEFAULT '',
                    created_at VARCHAR(64) NOT NULL,
                    INDEX idx_activity_created (created_at)
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carpool_history (
                    carpool_id INTEGER PRIMARY KEY,
                    flight_code TEXT NOT NULL DEFAULT '',
                    airport_code TEXT NOT NULL DEFAULT '',
                    flight_date TEXT NOT NULL DEFAULT '',
                    seats INTEGER NOT NULL DEFAULT 0,
                    peak_members INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT '',
                    closed_at TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    user_email TEXT NOT NULL DEFAULT '',
                    carpool_id INTEGER NOT NULL DEFAULT 0,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log (created_at)")

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
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'share_token'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN share_token VARCHAR(32) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM carpools LIKE 'direction'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpools ADD COLUMN direction VARCHAR(16) NOT NULL DEFAULT 'to_airport'")
                cur.execute("SHOW COLUMNS FROM carpool_history LIKE 'direction'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE carpool_history ADD COLUMN direction VARCHAR(16) NOT NULL DEFAULT 'to_airport'")
                for col in ("terms_version", "terms_accepted_at", "age_confirmed_at"):
                    cur.execute(f"SHOW COLUMNS FROM users LIKE '{col}'")
                    if not cur.fetchone():
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} VARCHAR(64) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM users LIKE 'last_login_at'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE users ADD COLUMN last_login_at VARCHAR(64) NOT NULL DEFAULT ''")
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
                if "share_token" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN share_token TEXT NOT NULL DEFAULT ''")
                if "direction" not in cols:
                    cur.execute("ALTER TABLE carpools ADD COLUMN direction TEXT NOT NULL DEFAULT 'to_airport'")
                cur.execute("PRAGMA table_info(carpool_history)")
                if "direction" not in [row[1] for row in cur.fetchall()]:
                    cur.execute("ALTER TABLE carpool_history ADD COLUMN direction TEXT NOT NULL DEFAULT 'to_airport'")
                cur.execute("PRAGMA table_info(users)")
                user_cols = [row[1] for row in cur.fetchall()]
                for col in ("last_login_at", "terms_version", "terms_accepted_at", "age_confirmed_at"):
                    if col not in user_cols:
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
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


ACTIVITY_RETENTION = timedelta(days=180)


def _log_event(event: str, email: str = "", carpool_id: int = 0, detail: str = "") -> None:
    """Record an activity event for the admin dashboard. Never breaks the caller."""
    try:
        p = db.placeholder
        db.execute(
            f"INSERT INTO activity_log (event, user_email, carpool_id, detail, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
            (event, email or "", int(carpool_id or 0), str(detail or "")[:255], _now_utc().isoformat()),
        )
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        app.logger.warning("Could not log %s event: %s", event, exc)


def _history_open(carpool: dict[str, Any]) -> None:
    """Start the analytics record for a carpool (idempotent)."""
    p = db.placeholder
    if db.query(f"SELECT carpool_id FROM carpool_history WHERE carpool_id = {p}", (carpool["id"],)):
        return
    db.execute(
        f"INSERT INTO carpool_history (carpool_id, flight_code, airport_code, flight_date, seats, peak_members, created_at, direction) "
        f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})",
        (
            carpool["id"],
            str(carpool.get("flight_code") or ""),
            str(carpool.get("airport_code") or ""),
            str(carpool.get("requested_flight_date") or ""),
            int(carpool.get("seats_available") or 0),
            1,
            str(carpool.get("created_at") or _now_utc().isoformat()),
            _direction(carpool),
        ),
    )


def _history_bump_peak(carpool_id: int) -> None:
    """Remember the largest the group ever got (members leave before departure)."""
    p = db.placeholder
    rows = db.query(f"SELECT COUNT(*) AS c FROM party_members WHERE carpool_id = {p}", (carpool_id,))
    count = int(rows[0]["c"]) if rows else 0
    db.execute(
        f"UPDATE carpool_history SET peak_members = {p} WHERE carpool_id = {p} AND peak_members < {p}",
        (count, carpool_id, count),
    )


def _history_close(carpool: dict[str, Any], outcome: str) -> None:
    """Mark a carpool as finished: departed, disbanded, abandoned or admin_deleted."""
    try:
        _history_open(carpool)
        _history_bump_peak(carpool["id"])
        p = db.placeholder
        db.execute(
            f"UPDATE carpool_history SET closed_at = {p}, outcome = {p} WHERE carpool_id = {p} AND closed_at = ''",
            (_now_utc().isoformat(), outcome, carpool["id"]),
        )
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        app.logger.warning("Could not record carpool %s outcome: %s", carpool.get("id"), exc)


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


def _direction(row: dict[str, Any]) -> str:
    value = str(row.get("direction") or "")
    return value if value in DIRECTIONS else DEFAULT_DIRECTION


CAMPUS_NAME = "UCR"


def _trip_label(row: dict[str, Any]) -> str:
    """'UCR → LAX' or 'LAX → UCR' for headings and messages."""
    airport = str(row.get("airport_code") or "").strip() or "Airport"
    if _direction(row) == "from_airport":
        return f"{airport} → {CAMPUS_NAME}"
    return f"{CAMPUS_NAME} → {airport}"


def _stored_date(stored: Any) -> Any:
    try:
        return datetime.strptime(str(stored or ""), "%m-%d-%Y").date()
    except ValueError:
        return None


def _friendly_date(stored: Any) -> str:
    """'10-23-2026' -> 'Fri, Oct 23'."""
    d = _stored_date(stored)
    return f"{d.strftime('%a, %b')} {d.day}" if d else _display_date(stored)


def _countdown(stored: Any) -> str:
    """'Today', 'Tomorrow', 'In 5 days' relative to today on campus (Pacific)."""
    d = _stored_date(stored)
    if not d:
        return ""
    n = (d - datetime.now(ZoneInfo("America/Los_Angeles")).date()).days
    if n == 0:
        return "Today"
    if n == 1:
        return "Tomorrow"
    if n == -1:
        return "Yesterday"
    return "Past" if n < 0 else f"In {n} days"


def _trip_headline(row: dict[str, Any]) -> str:
    """'UCR → ONT · Fri, Oct 23'."""
    return f"{_trip_label(row)} · {_friendly_date(row.get('requested_flight_date'))}"


def _display_date(stored: Any) -> str:
    """Stored dates are MM-DD-YYYY; show them US-style as MM/DD/YYYY."""
    return str(stored or "").replace("-", "/")


def _display_time(value: Any) -> str:
    """'14:30' -> '2:30 PM'."""
    try:
        return datetime.strptime(str(value), "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return ""


def _to_api_flight_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")




def _resolve_airport(code: str) -> tuple[str, str]:
    airport = AIRPORT_CODE_MAP.get(code)
    if airport:
        return airport["name"], airport["location"]
    return f"Airport {code}", "Unknown location"


def _serialize_entry(row: dict[str, Any], view: str = "public") -> dict[str, Any]:
    data = dict(row)
    hidden_fields = {"expires_at", "created_at", "fetched_from", "status", "share_token", "creator_email"}
    if view == "public":
        hidden_fields.update({"phone", "notes"})
    for field in hidden_fields:
        data.pop(field, None)
    data["direction"] = _direction(row)
    return data


def _member_email(carpool_id: int, data: dict[str, Any], id_key: str, email_key: str) -> str:
    """Resolve the member a request names. Pages send the party_members id
    (emails are not shared with other members); `email_key` is accepted for
    older cached pages."""
    p = db.placeholder
    raw_id = data.get(id_key)
    if raw_id not in (None, ""):
        try:
            member_id = int(raw_id)
        except (TypeError, ValueError):
            return ""
        rows = db.query(
            f"SELECT user_email FROM party_members WHERE id = {p} AND carpool_id = {p}", (member_id, carpool_id)
        )
        return rows[0]["user_email"] if rows else ""
    return str(data.get(email_key, "")).strip().lower()


def _mask_flight(code: str) -> str:
    """'UA1375' -> 'UA ••••': the airline shows, the number doesn't."""
    code = str(code or "")
    return f"{code[:2]} ••••" if code else ""


def _visible_flights(flights: list[str], reveal: str = "") -> list[str]:
    """Flights as a non-member sees them: masked, except the exact flight
    they searched for (spaces/case already removed by _clean_flight_code)."""
    return [f if reveal and f == reveal else _mask_flight(f) for f in flights]


MAX_CARPOOL_FLIGHTS = 10


def _flights_for(carpools: list[dict[str, Any]]) -> dict[int, list[str]]:
    """Every flight in each carpool: the organizer's first, then riders' in the
    order they were added, without duplicates. One query for all carpools."""
    out: dict[int, list[str]] = {}
    for c in carpools:
        own = str(c.get("flight_code") or "").strip()
        out[c["id"]] = [own] if own else []
    if not out:
        return out
    ids = list(out)
    marks = ", ".join([db.placeholder] * len(ids))
    try:
        rows = db.query(
            f"SELECT carpool_id, flight_code FROM carpool_flights WHERE carpool_id IN ({marks}) ORDER BY added_at, id",
            tuple(ids),
        )
    except Exception as exc:
        app.logger.warning("Could not load carpool flights: %s", exc)
        return out
    for r in rows:
        flights = out.get(r["carpool_id"])
        if flights is not None and r["flight_code"] not in flights:
            flights.append(r["flight_code"])
    return out


def _add_carpool_flight(carpool: dict[str, Any], flight_code: str) -> None:
    """Add a rider's flight to the carpool (not to the rider). No-op if known."""
    if not flight_code or flight_code in _flights_for([carpool]).get(carpool["id"], []):
        return
    p = db.placeholder
    count = db.query(f"SELECT COUNT(*) AS n FROM carpool_flights WHERE carpool_id = {p}", (carpool["id"],))
    if count and int(count[0]["n"]) >= MAX_CARPOOL_FLIGHTS:
        return
    try:
        db.execute(
            f"INSERT INTO carpool_flights (carpool_id, flight_code, added_at) VALUES ({p}, {p}, {p})",
            (carpool["id"], flight_code, _now_utc().isoformat()),
        )
    except Exception as exc:  # duplicate from a simultaneous join
        app.logger.info("Flight not added to carpool %s: %s", carpool["id"], exc)


def _parse_optional_flight(value: Any) -> tuple[str, str | None]:
    """(flight_code, error). Empty input is fine: the flight is optional."""
    code = _clean_flight_code(str(value or ""))
    if not code:
        return "", None
    if not FLIGHT_CODE_PATTERN.match(code):
        return "", "Flight number should look like UA1375."
    return code, None


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


def _short_name(raw_name: str, email: str = "") -> str:
    """'Keshav Pillarisetti' -> 'Keshav P.'. Other users only ever see a
    first name and last initial."""
    first_name, last_initial = _derive_user_name_parts(raw_name, email)
    return f"{first_name} {last_initial}." if last_initial else (first_name or "A member")


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


def _normalize_phone(value: Any) -> str:
    """Collapse all whitespace types (including non-breaking spaces from mobile
    browsers) into single regular spaces."""
    return re.sub(r"[\s\u00a0\u2000-\u200b\u202f\u205f\u3000]+", " ", str(value or "")).strip()


def _save_user_phone(email: str, phone: str) -> None:
    p = db.placeholder
    existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
    if existing:
        db.execute(f"UPDATE users SET phone = {p} WHERE user_email = {p}", (phone, email))
    else:
        first_name, last_initial = _derive_user_name_parts(session.get("user_name", ""), email)
        db.execute(
            f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
            (email, first_name, last_initial, phone, _now_utc().isoformat()),
        )


def _delete_carpool_party(carpool_id: int) -> None:
    p = db.placeholder
    db.execute(f"DELETE FROM carpool_flights WHERE carpool_id = {p}", (carpool_id,))
    db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (carpool_id,))
    db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))


def _remove_user_from_carpools(email: str, reason: str) -> None:
    """Take a user out of every carpool (for a ban or a deletion request).
    Carpools they organize are closed; the other riders are told it was
    closed, never why. `reason` goes to the admin activity log only."""
    p = db.placeholder
    for m in db.query(f"SELECT carpool_id FROM party_members WHERE user_email = {p}", (email,)):
        rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (m["carpool_id"],))
        if not rows:
            continue
        carpool = rows[0]
        if carpool.get("creator_email") == email:
            others = db.query(
                f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
                (carpool["id"], email),
            )
            for o in others:
                notify_user(
                    o["user_email"],
                    f"The carpool for {carpool.get('flight_code', '')} ({_trip_label(carpool)}) on "
                    f"{_display_date(carpool.get('requested_flight_date', ''))} was closed by Campus2Air.",
                )
            _history_close(carpool, "admin_deleted")
            _log_event("admin_deleted", "", carpool["id"], reason or "account removed")
            _delete_carpool_party(carpool["id"])
        else:
            db.execute(f"DELETE FROM party_members WHERE carpool_id = {p} AND user_email = {p}", (carpool["id"], email))


def _delete_user_data(email: str) -> None:
    """Delete a user's account data (deletion requests and inactive accounts).
    Suspensions are kept; see the Privacy Policy."""
    p = db.placeholder
    _remove_user_from_carpools(email, "")
    db.execute(f"DELETE FROM notifications WHERE user_email = {p}", (email,))
    db.execute(f"DELETE FROM join_log WHERE user_email = {p}", (email,))
    db.execute(f"DELETE FROM activity_log WHERE user_email = {p}", (email,))
    # Other people's events can mention them ("by x@ucr.edu", "from x@ucr.edu").
    db.execute(f"UPDATE activity_log SET detail = '' WHERE detail LIKE {p}", (f"%{email}%",))
    db.execute(f"DELETE FROM users WHERE user_email = {p}", (email,))


def _is_banned(email: str) -> bool:
    if not email:
        return False
    p = db.placeholder
    return bool(db.query(f"SELECT user_email FROM banned_users WHERE user_email = {p}", (email,)))


def _clear_all_data() -> None:
    db.execute("DELETE FROM banned_users")
    db.execute("DELETE FROM activity_log")
    db.execute("DELETE FROM carpool_history")
    db.execute("DELETE FROM join_log")
    db.execute("DELETE FROM carpool_flights")
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
    if isinstance(e, DatabaseUnavailable):
        message = "Campus2Air is temporarily unavailable. Please try again in a minute."
        if request.path.startswith("/api/") or request.path == "/auth/firebase-callback":
            return jsonify({"error": message}), 503
        return f"<h1>Temporarily unavailable</h1><p>{message}</p>", 503
    if isinstance(e, HTTPException):
        # 404/405/etc. are normal client errors, not server crashes.
        if request.path.startswith("/api/"):
            return jsonify({"error": e.name}), e.code
        if e.code == 404:
            return render_template("404.html"), 404
        return e
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
        expired = db.query(f"SELECT * FROM carpools WHERE expires_at != '' AND expires_at <= {p}", (now_iso,))
        if expired:
            for row in expired:
                _history_close(row, "departed")
                _log_event("departed", "", row["id"], f"{row.get('flight_code', '')} {row.get('airport_code', '')}")
                db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (row["id"],))
                db.execute(f"DELETE FROM carpool_flights WHERE carpool_id = {p}", (row["id"],))
            db.execute(f"DELETE FROM carpools WHERE expires_at != '' AND expires_at <= {p}", (now_iso,))
        # Join history is only needed for the rolling join limit window.
        join_log_cutoff = (_now_utc() - JOIN_LIMIT_WINDOW * 2).isoformat()
        db.execute(f"DELETE FROM join_log WHERE joined_at < {p}", (join_log_cutoff,))
        activity_cutoff = (_now_utc() - ACTIVITY_RETENTION).isoformat()
        db.execute(f"DELETE FROM activity_log WHERE created_at < {p}", (activity_cutoff,))
        # Retention promised in the Privacy Policy: notifications go after
        # 90 days; accounts with no carpools go after a year without sign-in.
        notif_cutoff = (_now_utc() - NOTIFICATION_RETENTION).isoformat()
        db.execute(f"DELETE FROM notifications WHERE created_at < {p}", (notif_cutoff,))
        # Suspension records: up to 3 years (Privacy Policy §5).
        ban_cutoff = (_now_utc() - SUSPENSION_RETENTION).isoformat()
        db.execute(f"DELETE FROM banned_users WHERE banned_at < {p}", (ban_cutoff,))
        inactive_cutoff = (_now_utc() - INACTIVE_ACCOUNT_RETENTION).isoformat()
        stale = db.query(
            f"SELECT user_email FROM users WHERE "
            f"(CASE WHEN last_login_at IS NULL OR last_login_at = '' THEN created_at ELSE last_login_at END) < {p}",
            (inactive_cutoff,),
        )
        for row in stale:
            in_carpool = db.query(f"SELECT id FROM party_members WHERE user_email = {p}", (row["user_email"],))
            if not in_carpool:
                _delete_user_data(row["user_email"])
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        app.logger.warning("Expired entry cleanup failed: %s", exc)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.get("/")
def landing() -> Any:
    base = _public_base_url()
    og = {
        "title": "Campus2Air: share rides to and from the airport",
        "description": "UCR students: find classmates on the same flight or heading the same way, and split the ride.",
        "url": f"{base}/",
        "image": f"{base}{url_for('static', filename='og-image.jpg')}",
    }
    return render_template("welcome.html", og=og)


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


def _safe_next_path(value: Any) -> str:
    """Only allow redirecting back to an invite page (no open redirects)."""
    path = str(value or "")
    return path if INVITE_PATH_PATTERN.match(path) else ""


@app.get("/login")
def login_page() -> Any:
    if session.get("user_email"):
        next_path = _safe_next_path(session.pop("post_login_next", ""))
        return redirect(next_path or url_for("start_now_page"))
    error = request.args.get("error", "")
    return render_template("login.html", error=error)


@app.post("/auth/firebase-callback")
def firebase_callback() -> Any:
    """Receive a Firebase ID token from the client, verify it, and set session."""
    data = request.get_json(silent=True) or {}
    firebase_id_token = str(data.get("idToken", "")).strip()

    if not firebase_id_token:
        return jsonify({"error": "Missing Firebase ID token"}), 400
    # Clickwrap: signing in requires agreeing to the current Terms version
    # and confirming the user is 18 or older (two separate checkboxes).
    if str(data.get("termsVersion", "")) != TERMS_VERSION:
        return jsonify({
            "error": "Please review and agree to the updated Terms of Service to continue.",
            "terms_required": True,
            "terms_version": TERMS_VERSION,
        }), 409
    if data.get("ageConfirmed") is not True:
        return jsonify({
            "error": "Please confirm that you are 18 or older to continue.",
            "age_required": True,
        }), 409

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
    if _is_banned(email):
        return jsonify({"error": BANNED_MESSAGE, "banned": True}), 403

    # Record the profile and the Terms acceptance before signing the user in.
    # If the acceptance can't be saved, the sign-in fails: nobody should be
    # signed in without a record of what they agreed to.
    first_name, last_initial = _derive_user_name_parts(name, email)
    p = db.placeholder
    now_iso = _now_utc().isoformat()
    try:
        existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
        if not existing:
            db.execute(
                f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at, last_login_at, terms_version, terms_accepted_at, age_confirmed_at) "
                f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})",
                (email, first_name, last_initial, "", now_iso, now_iso, TERMS_VERSION, now_iso, now_iso),
            )
            is_new = True
        else:
            db.execute(
                f"UPDATE users SET first_name = {p}, last_initial = {p}, last_login_at = {p} WHERE user_email = {p}",
                (first_name, last_initial, now_iso, email),
            )
            # Record acceptance only when the version changes, so the date
            # shows when this version was first agreed to. (NULL != x is never
            # true in SQL, so older rows with no version need the IS NULL.)
            db.execute(
                f"UPDATE users SET terms_version = {p}, terms_accepted_at = {p} "
                f"WHERE user_email = {p} AND (terms_version IS NULL OR terms_version != {p})",
                (TERMS_VERSION, now_iso, email, TERMS_VERSION),
            )
            # The 18+ confirmation is given at every sign-in; keep the latest.
            db.execute(f"UPDATE users SET age_confirmed_at = {p} WHERE user_email = {p}", (now_iso, email))
            is_new = False
        saved = db.query(f"SELECT terms_version, age_confirmed_at FROM users WHERE user_email = {p}", (email,))
        if not saved or saved[0].get("terms_version") != TERMS_VERSION or not saved[0].get("age_confirmed_at"):
            raise RuntimeError("terms acceptance was not stored")
    except Exception as exc:
        app.logger.error("Sign-in refused, could not record Terms acceptance for %s: %s", email, exc)
        return jsonify({"error": "We couldn't finish signing you in. Please try again in a minute."}), 503

    # Remember an invite link the user was sent to before signing in.
    next_path = _safe_next_path(session.get("post_login_next", ""))
    for key in USER_SESSION_KEYS:
        session.pop(key, None)
    session.permanent = True
    session["user_email"] = email
    session["user_name"] = name
    session["user_uid"] = uid
    _get_csrf_token()

    try:
        if is_new:
            _log_event("signup", email)
        _log_event("login", email)
    except Exception as exc:
        app.logger.warning("Could not log sign-in for %s: %s", email, exc)

    return jsonify({"ok": True, "redirect": next_path or url_for("start_now_page")})


@app.get("/auth/logout")
def user_logout_link() -> Any:
    # Logging out changes state, so it requires a CSRF-protected POST. A plain
    # link (old bookmark, or an <img> on another site) just goes home.
    return redirect(url_for("login_page"))


@app.post("/auth/logout")
def user_logout() -> Any:
    for key in USER_SESSION_KEYS:
        session.pop(key, None)
    return render_template("logout.html")


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
    except DatabaseUnavailable:
        raise
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

    airport_code = str(data["airport_code"]).upper().strip()
    if not AIRPORT_CODE_PATTERN.match(airport_code):
        return jsonify({"error": "airport_code must be a 3-letter airport code"}), 400

    raw_flight_code = str(data["flight_code"]).strip().upper()
    if " " in raw_flight_code:
        return jsonify({"error": "flight_code must not contain spaces (use UA533 format)"}), 400

    flight_code = _clean_flight_code(raw_flight_code)
    if not FLIGHT_CODE_PATTERN.match(flight_code):
        return jsonify({"error": "Invalid flight_code format. Example: UA533"}), 400


    raw_phone = _normalize_phone(data["phone"])
    if not PHONE_PATTERN.match(raw_phone):
        return jsonify({"error": "Phone must be in format +1 (AAA) BBB CCCC"}), 400

    departure_date_raw = str(data.get("departure_date") or data.get("flight_date") or "").strip()
    parsed_flight_date = _parse_user_flight_date(departure_date_raw)
    if not parsed_flight_date:
        return jsonify({"error": "departure_date must be in MM-DD-YYYY format"}), 400
    flight_date_user = _to_user_flight_date(parsed_flight_date)
    # Allow "yesterday" in UTC so users west of UTC can still post for their local today.
    today_utc = _now_utc().date()
    if parsed_flight_date.date() < today_utc - timedelta(days=1):
        return jsonify({"error": "Departure date cannot be in the past"}), 400
    if parsed_flight_date.date() > today_utc + timedelta(days=MAX_DAYS_AHEAD):
        return jsonify({"error": "Departure date must be within the next year"}), 400

    notes = str(data.get("notes", "")).strip()
    if len(notes) > MAX_NOTES_LENGTH:
        return jsonify({"error": f"Notes must be {MAX_NOTES_LENGTH} characters or fewer"}), 400

    seats_available = _parse_seat_count(data.get("seats_available", 4))
    if seats_available is None:
        return jsonify({"error": "seats_available must be a whole number between 1 and 7"}), 400

    planned_departure_time = _normalize_departure_time(data.get("planned_departure_time", ""))
    if planned_departure_time is None:
        return jsonify({"error": "planned_departure_time must use HH:MM format"}), 400
    if not planned_departure_time:
        return jsonify({"error": "Planned Leave Time is required"}), 400

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()

    # Expire at 11:59 PM UTC the day AFTER the departure date.
    # +1 day buffer prevents users in UTC-offset timezones (e.g. California, UTC-8)
    # from having their carpool instantly cleaned up when their local "today"
    # is already "yesterday" in UTC.
    expires_at = (parsed_flight_date + timedelta(days=1)).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).isoformat()

    direction = str(data.get("direction") or DEFAULT_DIRECTION).strip()
    if direction not in DIRECTIONS:
        return jsonify({"error": "direction must be to_airport or from_airport"}), 400

    destination_airport = str(data.get("destination_airport", "")).strip().upper()
    if destination_airport and not AIRPORT_CODE_PATTERN.match(destination_airport):
        return jsonify({"error": "destination_airport must be a 3-letter airport code"}), 400

    p = db.placeholder
    last_id = db.execute(
        f"""
        INSERT INTO carpools (
            first_name,last_initial,phone,flight_code,airport_code,airport_name,airport_location,
            flight_time_utc,flight_date_utc,seats_available,notes,fetched_from,status,expires_at,created_at,
            requested_flight_date,destination_airport,planned_departure_time,creator_email,direction
        ) VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
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
            notes,
            "direct",
            "active",
            expires_at,
            created_at,
            flight_date_user,
            destination_airport,
            planned_departure_time,
            creator_email,
            direction,
        ),
    )

    # Keep the phone on the creator's profile too: the member list reads phones
    # from the users table, so without this other members can't see it.
    try:
        _save_user_phone(creator_email, raw_phone)
    except Exception as exc:
        app.logger.warning("Could not save creator phone: %s", exc)

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

    try:
        _history_open(rows[0])
    except Exception as exc:
        app.logger.warning("Could not start carpool history: %s", exc)
    _log_event("created", creator_email, last_id, f"{flight_code} {_trip_label({'direction': direction, 'airport_code': airport_code})} {_display_date(flight_date_user)}")

    if creator_email:
        try:
            notify_user(creator_email, f"Your carpool for {flight_code} ({_trip_label({'direction': direction, 'airport_code': airport_code})}) on {_display_date(flight_date_user)} has been created! Share your carpool to find ride partners.")
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
    planned_leave_time_raw = request.args.get("planned_leave_time", "").strip()
    planned_leave_time = _normalize_departure_time(planned_leave_time_raw) if planned_leave_time_raw else ""
    if flight_date_raw and not parsed_search_date:
        return jsonify({"error": "departure_date must be in MM-DD-YYYY format", "count": 0, "results": []}), 400
    if planned_leave_time is None:
        return jsonify({"error": "planned_leave_time must use HH:MM format", "count": 0, "results": []}), 400
    # Direction is a hard filter when given: someone landing at LAX should
    # never see carpools heading to LAX. Omitted = both (older clients).
    direction = request.args.get("direction", "").strip()
    if direction and direction not in DIRECTIONS:
        return jsonify({"error": "direction must be to_airport or from_airport", "count": 0, "results": []}), 400

    # Require at least one search field
    if not flight_code and not airport_code and not flight_date_raw and not planned_leave_time:
        return jsonify({"error": "At least 1 search field is required", "count": 0, "results": []}), 400
    # A leave time only means something on a given day.
    if planned_leave_time and not flight_date:
        return jsonify({"error": "Pick a date to search by time.", "count": 0, "results": []}), 400

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

    def _time_to_minutes(t: str) -> int | None:
        try:
            h, m = t.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return None

    total_criteria = sum([bool(flight_code), bool(airport_code), bool(flight_date), bool(planned_leave_time)])
    flights_by_carpool = _flights_for(rows)

    results: list[dict[str, Any]] = []
    # Matches skipped only because they go the other way, so the page can
    # point out "1 Airport → Campus carpool matches" instead of nothing.
    other_direction_count = 0
    for entry in rows:
        wrong_direction = bool(direction) and _direction(entry) != direction
        matched_fields = 0
        reasons: list[str] = []
        if flight_code and entry["flight_code"] == flight_code:
            matched_fields += 1
            reasons.append("Exact flight code match")
        elif flight_code and flight_code in flights_by_carpool.get(entry["id"], []):
            matched_fields += 1
            reasons.append("A rider is on this flight")
        if airport_code and entry["airport_code"] == airport_code:
            matched_fields += 1
            reasons.append("Same airport code")
        if flight_date and entry.get("requested_flight_date") == flight_date:
            matched_fields += 1
            reasons.append("Same requested flight date")
        if planned_leave_time:
            # Leave time is a hard filter: only carpools leaving within the
            # window of the searched time are shown. A time window is only
            # meaningful for one day, so the date must match too if given.
            if flight_date and entry.get("requested_flight_date") != flight_date:
                continue
            search_mins = _time_to_minutes(planned_leave_time)
            entry_mins = _time_to_minutes(str(entry.get("planned_departure_time") or ""))
            if search_mins is None or entry_mins is None or abs(search_mins - entry_mins) > LEAVE_TIME_WINDOW_MINUTES:
                continue
            matched_fields += 1
            reasons.append("Leaves within 2 hours")
        if matched_fields > 0 and wrong_direction:
            other_direction_count += 1
            continue
        if matched_fields > 0:
            score = round((matched_fields / total_criteria) * 100) if total_criteria > 0 else 0
            public_row = _serialize_entry(entry)
            public_row["match_score"] = score
            all_flights = flights_by_carpool.get(entry["id"], [])
            if entry["id"] in user_memberships:
                public_row["flights"] = all_flights
            else:
                # Non-members only see the flight they searched for.
                public_row["flights"] = _visible_flights(all_flights, flight_code)
                public_row["flight_code"] = _visible_flights([entry["flight_code"]], flight_code)[0]
            public_row["match_reasons"] = reasons
            cid = entry["id"]
            mc = member_counts.get(cid, 0)
            public_row["member_count"] = mc
            public_row["seats_remaining"] = max(0, int(entry.get("seats_available", 3)) - mc)
            public_row["is_member"] = cid in user_memberships
            public_row["is_creator"] = entry.get("creator_email") == current_user
            public_row["direction"] = _direction(entry)
            results.append(public_row)

    results.sort(key=lambda r: r["match_score"], reverse=True)
    return jsonify({"count": len(results), "results": results, "other_direction_count": other_direction_count})


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


def _carpool_members(carpool_id: int) -> list[dict[str, Any]]:
    """Members in join order with display names (no emails or phones)."""
    p = db.placeholder
    rows = db.query(
        f"SELECT pm.user_email, u.first_name, u.last_initial FROM party_members pm "
        f"LEFT JOIN users u ON u.user_email = pm.user_email "
        f"WHERE pm.carpool_id = {p} ORDER BY pm.joined_at",
        (carpool_id,),
    )
    members = []
    for row in rows:
        first = str(row.get("first_name") or "").strip() or str(row["user_email"]).split("@")[0]
        initial = str(row.get("last_initial") or "").strip()[:1]
        members.append({"email": row["user_email"], "name": f"{first} {initial}." if initial else first})
    return members


@app.get("/api/carpools/<int:carpool_id>/share-link")
def carpool_share_link(carpool_id: int) -> Any:
    """Return the invite link for a carpool. Any member may share it."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    rows = db.query(f"SELECT id, share_token FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    member = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (carpool_id, email),
    )
    if not member:
        return jsonify({"error": "Only carpool members can share this carpool"}), 403
    token = str(rows[0].get("share_token") or "")
    if not token:
        # Random and unguessable, so carpools can't be found by counting ids.
        token = secrets.token_urlsafe(12)
        db.execute(
            f"UPDATE carpools SET share_token = {p} WHERE id = {p} AND share_token = ''",
            (token, carpool_id),
        )
        # If two members created a token at the same moment, use the winner.
        token = db.query(f"SELECT share_token FROM carpools WHERE id = {p}", (carpool_id,))[0]["share_token"]
    _log_event("shared", email, carpool_id)
    return jsonify({"path": url_for("invite_page", token=token)})


def _public_base_url() -> str:
    """Absolute site URL for link previews (crawlers need absolute URLs)."""
    if PUBLIC_APP_URL:
        return PUBLIC_APP_URL
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    scheme = "https" if proto == "https" or os.getenv("VERCEL") else request.scheme
    return f"{scheme}://{request.host}"


def _invite_preview(entry: dict[str, Any] | None, member_count: int = 0) -> dict[str, str]:
    """Title/description for the link preview shown in WhatsApp, Messages, etc.

    Shown to anyone who has the link, signed in or not, so it contains trip
    details only: no names, emails, phone numbers, notes or flight numbers.
    """
    base = _public_base_url()
    meta = {"url": f"{base}{request.path}", "image": f"{base}{url_for('static', filename='og-image.jpg')}"}
    if not entry:
        meta.update(title="This carpool is no longer available",
                    description="It may have been disbanded or already departed. Find another ride on Campus2Air.")
        return meta
    airport = entry.get("airport_code", "")
    d = _stored_date(entry.get("requested_flight_date"))
    date_text = f"{d.strftime('%a, %b')} {d.day}, {d.year}" if d else _display_date(entry.get("requested_flight_date"))
    from_airport = _direction(entry) == "from_airport"
    route = _trip_label(entry)
    time_text = _display_time(entry.get("planned_departure_time"))
    seats = int(entry.get("seats_available") or 0)
    left = max(0, seats - member_count)
    parts = []
    if time_text:
        parts.append(f"Meets at {airport} at {time_text}" if from_airport else f"Leaves campus at {time_text}")
    parts.append(f"{left} of {seats} seats left" if left else "Currently full")
    parts.append("UCR students: sign in with your @ucr.edu account to see details and join.")
    meta.update(title=f"{route} carpool · {date_text}",
                description=" · ".join(parts))
    return meta


@app.get("/c/<token>")
def invite_page(token: str) -> Any:
    """Shared invite link: summary of a carpool and an offer to join it."""
    if not SHARE_TOKEN_PATTERN.match(token):
        return render_template("invite.html", carpool=None, og=_invite_preview(None), **_user_context()), 404

    _cleanup_expired_entries()
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE share_token = {p}", (token,))

    if not _require_user_login():
        # Link-preview crawlers (WhatsApp, Messages, iMessage, Slack...) and
        # logged-out visitors get a public preview page with Open Graph tags
        # instead of a redirect, so the shared link shows the trip. They're
        # brought back here after signing in.
        session["post_login_next"] = request.path
        if not rows:
            return render_template("invite_preview.html", carpool=None, og=_invite_preview(None)), 404
        entry = rows[0]
        count_rows = db.query(f"SELECT COUNT(*) AS c FROM party_members WHERE carpool_id = {p}", (entry["id"],))
        member_count = int(count_rows[0]["c"]) if count_rows else 0
        seats = int(entry.get("seats_available") or 0)
        preview = {
            "headline": _trip_headline(entry),
            "direction": _direction(entry),
            "countdown": _countdown(entry.get("requested_flight_date")),
            "date": _display_date(entry.get("requested_flight_date")),
            "time": _display_time(entry.get("planned_departure_time")) or "—",
            "airport_code": entry.get("airport_code", ""),
            "seats_total": seats,
            "seats_left": max(0, seats - member_count),
            "member_count": member_count,
        }
        return render_template("invite_preview.html", carpool=preview, og=_invite_preview(entry, member_count))

    if not rows:
        return render_template("invite.html", carpool=None, og=_invite_preview(None), **_user_context()), 404

    entry = rows[0]
    email = session.get("user_email", "")
    members = _carpool_members(entry["id"])
    seats_total = int(entry.get("seats_available") or 0)
    flight_code = str(entry.get("flight_code") or "")
    airline = AIRLINE_CODES.get(flight_code[:2], "")
    first_name = str(entry.get("first_name") or "")
    last_initial = str(entry.get("last_initial") or "")
    organizer = f"{first_name} {last_initial}." if last_initial else first_name
    profile = db.query(f"SELECT phone FROM users WHERE user_email = {p}", (email,))

    carpool = {
        "id": entry["id"],
        "organizer": organizer,
        "flight_code": flight_code if any(m["email"] == email for m in members) else _mask_flight(flight_code),
        "airline": airline,
        "airport_code": entry.get("airport_code", ""),
        "airport_name": entry.get("airport_name", ""),
        "direction": _direction(entry),
        "headline": _trip_headline(entry),
        "countdown": _countdown(entry.get("requested_flight_date")),
        "date": _display_date(entry.get("requested_flight_date", "")),
        "leave_time": _display_time(entry.get("planned_departure_time", "")),
        # Members see every flight; others see "UA ••••" (Privacy Policy §3).
        "flights": (_flights_for([entry]).get(entry["id"], [])
                    if any(m["email"] == email for m in members)
                    else _visible_flights(_flights_for([entry]).get(entry["id"], []))),
        # Notes are for members only (Privacy Policy §3).
        "notes": entry.get("notes", "") if any(m["email"] == email for m in members) else "",
        "members": [{"name": m["name"], "is_organizer": m["email"] == entry.get("creator_email")} for m in members],
        "member_count": len(members),
        "seats_total": seats_total,
        "seats_left": max(0, seats_total - len(members)),
        "is_member": any(m["email"] == email for m in members),
        "is_organizer": entry.get("creator_email") == email,
    }
    return render_template(
        "invite.html",
        carpool=carpool,
        og=_invite_preview(entry, len(members)),
        user_phone=(profile[0].get("phone") if profile else "") or "",
        **_user_context(),
    )


@app.post("/api/carpools/<int:carpool_id>/join")
def join_party(carpool_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    # Store phone number if provided
    p = db.placeholder
    data = request.get_json(silent=True) or {}
    join_flight, flight_error = _parse_optional_flight(data.get("flight_code"))
    if flight_error:
        return jsonify({"error": flight_error}), 400
    raw_phone = _normalize_phone(data.get("phone", ""))
    if raw_phone and PHONE_PATTERN.match(raw_phone):
        try:
            _save_user_phone(email, raw_phone)
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
        now = _now_utc()
        expires_at = str(carpool.get("expires_at") or "")
        if carpool.get("status") != "active" or (expires_at and expires_at <= now.isoformat()):
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

        # Rolling join limit. Leaving doesn't remove log rows, so join/leave
        # loops (notification spam, phone-number harvesting) are capped.
        window_start = now - JOIN_LIMIT_WINDOW
        recent_joins_sql = db._normalized_sql(
            f"SELECT joined_at FROM join_log WHERE user_email = {p} AND joined_at > {p} ORDER BY joined_at ASC",
            conn,
        )
        cur.execute(recent_joins_sql, (email, window_start.isoformat()))
        recent_joins = [dict(r) for r in cur.fetchall()]
        if len(recent_joins) >= JOIN_LIMIT_COUNT:
            conn.rollback()
            oldest = datetime.fromisoformat(str(recent_joins[0]["joined_at"]))
            retry_at = oldest + JOIN_LIMIT_WINDOW
            hours_left = max(1, int((retry_at - now).total_seconds() // 3600) + 1)
            return jsonify({
                "error": (
                    f"You can join up to {JOIN_LIMIT_COUNT} carpools every 24 hours. "
                    f"Try again in about {hours_left} hour{'s' if hours_left != 1 else ''}."
                ),
                "retry_at": retry_at.isoformat(),
            }), 429

        joined_at = now.isoformat()
        insert_sql = db._normalized_sql(
            f"INSERT INTO party_members (carpool_id, user_email, joined_at) VALUES ({p}, {p}, {p})",
            conn,
        )
        cur.execute(insert_sql, (carpool_id, email, joined_at))
        log_sql = db._normalized_sql(
            f"INSERT INTO join_log (user_email, carpool_id, joined_at) VALUES ({p}, {p}, {p})",
            conn,
        )
        cur.execute(log_sql, (email, carpool_id, joined_at))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()

    source = "invite" if data.get("source") == "invite" else "search"
    _log_event("joined", email, carpool_id, f"via {source}")
    if join_flight and carpool:
        try:
            _add_carpool_flight(carpool, join_flight)
        except Exception as exc:
            app.logger.warning("Could not save flight for carpool %s: %s", carpool_id, exc)
    try:
        _history_bump_peak(carpool_id)
    except Exception as exc:
        app.logger.warning("Could not update carpool peak size: %s", exc)

    # Notify creator and all existing members that someone joined
    joiner_name = _short_name(session.get("user_name", ""), email)
    flight_code = carpool.get("flight_code", "") if carpool else ""
    flight_date = _display_date(carpool.get("requested_flight_date", "")) if carpool else ""
    msg = f"{joiner_name} joined your carpool for {flight_code} ({_trip_label(carpool or {})}) on {flight_date}."
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
    _log_event("left", email, carpool_id)

    # Non-creators can leave immediately.
    if not is_creator:
        creator_email = carpool.get("creator_email", "")
        flight_code = carpool.get("flight_code", "")
        flight_date = _display_date(carpool.get("requested_flight_date", ""))
        leaver_name = _short_name(session.get("user_name", ""), email)
        if creator_email:
            try:
                notify_user(creator_email, f"{leaver_name} left your carpool for {flight_code} ({_trip_label(carpool)}) on {flight_date}.")
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
        _history_close(carpool, "abandoned")
        _delete_carpool_party(carpool_id)
        return jsonify({"ok": True, "message": "You left and the carpool was disbanded because no members remained."})

    # Transfer to the earliest-joined remaining member.
    new_owner_email = remaining_members[0]["user_email"]
    _log_event("transferred", new_owner_email, carpool_id, f"from {email}")
    first_name, last_initial, phone = _owner_profile_fields(new_owner_email)
    db.execute(
        f"UPDATE carpools SET creator_email = {p}, first_name = {p}, last_initial = {p}, phone = {p} WHERE id = {p}",
        (new_owner_email, first_name, last_initial, phone, carpool_id),
    )

    flight_code = carpool.get("flight_code", "")
    flight_date = _display_date(carpool.get("requested_flight_date", ""))
    try:
        notify_user(new_owner_email, f"You are now the organizer of the carpool for {flight_code} ({_trip_label(carpool)}) on {flight_date}. The previous creator left.")
        _notify_new_organizer_to_members(carpool_id, new_owner_email, first_name, last_initial, flight_code, flight_date)
    except Exception:
        pass
    return jsonify({"ok": True, "message": f"Left the carpool. Ownership transferred to {new_owner_email}."})


def _notify_new_organizer_to_members(
    carpool_id: int, new_owner_email: str, first_name: str, last_initial: str, flight_code: str, flight_date: str
) -> None:
    p = db.placeholder
    members = db.query(
        f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
        (carpool_id, new_owner_email),
    )
    owner_name = f"{first_name} {last_initial}.".strip() if last_initial else first_name
    for m in members:
        notify_user(m["user_email"], f"{owner_name} is now the organizer of the carpool for {flight_code} on {flight_date}.")


@app.post("/api/carpools/<int:carpool_id>/transfer-and-leave")
def transfer_and_leave(carpool_id: int) -> Any:
    """Creator picks a specific member to receive ownership, then leaves."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json(silent=True) or {}
    new_owner_email = _member_email(carpool_id, data, "new_owner_member_id", "new_owner_email")
    if not new_owner_email:
        return jsonify({"error": "Choose a current member to become the organizer"}), 400
    if new_owner_email == email:
        return jsonify({"error": "Choose another member to become the organizer"}), 400
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
    _log_event("left", email, carpool_id)
    _log_event("transferred", new_owner_email, carpool_id, f"from {email}")
    carpool_row = rows[0]
    flight_code = carpool_row.get("flight_code", "")
    flight_date = _display_date(carpool_row.get("requested_flight_date", ""))
    try:
        notify_user(new_owner_email, f"You are now the organizer of the carpool for {flight_code} ({_trip_label(carpool_row)}) on {flight_date}.")
        _notify_new_organizer_to_members(carpool_id, new_owner_email, fn, li, flight_code, flight_date)
    except Exception:
        pass
    return jsonify({"ok": True, "message": "Ownership transferred. You have left the carpool."})


@app.post("/api/carpools/<int:carpool_id>/flights")
def add_carpool_flight(carpool_id: int) -> Any:
    """A member adds their flight number to the carpool's flight list."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404
    member = db.query(
        f"SELECT id FROM party_members WHERE carpool_id = {p} AND user_email = {p}", (carpool_id, email)
    )
    if not member:
        return jsonify({"error": "Only members can add flights"}), 403
    data = request.get_json(silent=True) or {}
    code, error = _parse_optional_flight(data.get("flight_code"))
    if error or not code:
        return jsonify({"error": error or "Enter a flight number, like UA1375."}), 400
    _add_carpool_flight(rows[0], code)
    return jsonify({"ok": True, "flights": _flights_for(rows).get(carpool_id, [])})


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
            f"SELECT id, user_email, joined_at FROM party_members WHERE carpool_id = {p} ORDER BY joined_at",
            (cid,),
        )
        # Enrich members with name and phone from users table. Emails stay on
        # the server: members are identified to each other by member_id.
        enriched_members = []
        for m in members:
            member_data = {
                "member_id": m["id"],
                "joined_at": m["joined_at"],
                "is_me": m["user_email"] == email,
                "is_organizer": m["user_email"] == carpool.get("creator_email"),
            }
            try:
                user_rows = db.query(f"SELECT first_name, last_initial, phone FROM users WHERE user_email = {p}", (m["user_email"],))
                if user_rows:
                    member_data["first_name"] = user_rows[0]["first_name"]
                    member_data["last_initial"] = user_rows[0]["last_initial"]
                    member_data["phone"] = user_rows[0]["phone"]
                else:
                    member_data["first_name"] = "Member"
                    member_data["last_initial"] = ""
                    member_data["phone"] = ""
            except Exception:
                member_data["first_name"] = "Member"
                member_data["last_initial"] = ""
                member_data["phone"] = ""
            enriched_members.append(member_data)
        serialized = _serialize_entry(carpool, view="member")
        serialized["flights"] = _flights_for([carpool]).get(carpool["id"], [])
        parties.append({
            "carpool": serialized,
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
    raw_phone = _normalize_phone(data.get("phone", ""))
    if not PHONE_PATTERN.match(raw_phone):
        return jsonify({"error": "Phone must be in format +1 (AAA) BBB CCCC"}), 400
    try:
        _save_user_phone(email, raw_phone)
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
    target_email = _member_email(carpool_id, data, "member_id", "email")
    reason = str(data.get("reason", "")).strip()
    if not target_email:
        return jsonify({"error": "That member is not in this carpool"}), 404
    if not reason:
        return jsonify({"error": "A reason for removal is required"}), 400
    if len(reason) > MAX_REASON_LENGTH:
        return jsonify({"error": f"Reason must be {MAX_REASON_LENGTH} characters or fewer"}), 400
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
    _log_event("removed", target_email, carpool_id, f"by {email}: {reason}")
    carpool = rows[0]
    creator_name = f"{carpool.get('first_name', '')} {carpool.get('last_initial', '')}."
    flight_code = carpool.get("flight_code", "")
    flight_date = _display_date(carpool.get("requested_flight_date", ""))
    try:
        notify_user(
            target_email,
            f"You were removed from the carpool for {flight_code} ({_trip_label(carpool)}) on {flight_date} by {creator_name}. Reason: {reason}",
        )
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
        if not normalized_time:
            return jsonify({"error": "Planned Leave Time is required"}), 400
        updates.append(f"planned_departure_time = {p}")
        params.append(normalized_time)
        changed.append("departure time")
    if "notes" in data:
        notes = str(data["notes"]).strip()
        if len(notes) > MAX_NOTES_LENGTH:
            return jsonify({"error": f"Notes must be {MAX_NOTES_LENGTH} characters or fewer"}), 400
        updates.append(f"notes = {p}")
        params.append(notes)
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
        flight_date = _display_date(carpool.get("requested_flight_date", ""))
        changed_str = ", ".join(changed)
        members = db.query(
            f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
            (carpool_id, email),
        )
        for m in members:
            try:
                notify_user(m["user_email"], f"The carpool for {flight_code} ({_trip_label(carpool)}) on {flight_date} has been updated ({changed_str}) by the organizer.")
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
    if len(reason) > MAX_REASON_LENGTH:
        return jsonify({"error": f"Reason must be {MAX_REASON_LENGTH} characters or fewer"}), 400
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
            notify_user(m["user_email"], f"The carpool for flight {flight} ({_trip_label(carpool)}) created by {creator_name} has been disbanded. Reason: {reason}")
        except Exception:
            pass
    _history_close(carpool, "disbanded")
    _log_event("disbanded", email, carpool_id, reason)
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


@app.post("/api/notifications/dismiss-all")
def dismiss_all_notifications() -> Any:
    """Dismiss every unread notification for the current user."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    p = db.placeholder
    db.execute(
        f"UPDATE notifications SET dismissed = 1 WHERE user_email = {p} AND dismissed = 0",
        (email,),
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
    # Usernames are matched case-insensitively and trimmed: phone keyboards
    # auto-capitalize ("Admin") and autofill can add spaces. Passwords are exact.
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    username_ok = hmac.compare_digest(username, ADMIN_USERNAME.lower())
    if username_ok and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session.permanent = True
        session["admin_authed"] = True
        session["admin_login_at"] = _now_utc().isoformat()
        return redirect(url_for("admin_panel"))
    return redirect(url_for("admin_login_page", error=1))


CAMPUS_TZ = ZoneInfo("America/Los_Angeles")
ADMIN_RANGES = (7, 30, 90)
EVENT_LABELS = {
    "signup": "signed up",
    "login": "logged in",
    "created": "created a carpool",
    "joined": "joined a carpool",
    "left": "left a carpool",
    "removed": "was removed from a carpool",
    "transferred": "became organizer",
    "disbanded": "disbanded a carpool",
    "shared": "shared an invite link",
    "departed": "Carpool departed",
    "admin_deleted": "Admin deleted a carpool",
    "banned": "was suspended",
    "unbanned": "was unsuspended",
    "user_deleted": "Admin deleted a student's data",
}
OUTCOME_LABELS = {
    "departed": "Departed",
    "disbanded": "Disbanded",
    "abandoned": "Everyone left",
    "admin_deleted": "Deleted by admin",
}


def _local_dt(iso: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CAMPUS_TZ)


def _fmt_local(iso: Any) -> str:
    dt = _local_dt(iso)
    return dt.strftime("%m/%d/%Y %I:%M %p").replace(" 0", " ") if dt else "—"


def _admin_dashboard(days: int) -> dict[str, Any]:
    """Everything the admin dashboard shows, for the last `days` days (campus time)."""
    today = datetime.now(CAMPUS_TZ).date()
    start = today - timedelta(days=days - 1)
    day_keys = [start + timedelta(days=i) for i in range(days)]

    def in_range(iso: Any) -> bool:
        dt = _local_dt(iso)
        return bool(dt and dt.date() >= start)

    def per_day(isos: list[Any]) -> list[dict[str, Any]]:
        counts = {d: 0 for d in day_keys}
        for iso in isos:
            dt = _local_dt(iso)
            if dt and dt.date() in counts:
                counts[dt.date()] += 1
        return [{"label": d.strftime("%m/%d"), "value": counts[d]} for d in day_keys]

    users = db.query("SELECT * FROM users ORDER BY created_at DESC")
    carpools = db.query("SELECT * FROM carpools ORDER BY created_at DESC")
    members = db.query("SELECT carpool_id, user_email, joined_at FROM party_members ORDER BY joined_at")

    # Carpools created before history tracking existed get a record now.
    known = {r["carpool_id"] for r in db.query("SELECT carpool_id FROM carpool_history")}
    for c in carpools:
        if c["id"] not in known:
            try:
                _history_open(c)
                _history_bump_peak(c["id"])
            except Exception as exc:
                app.logger.warning("History backfill failed for %s: %s", c["id"], exc)
    history = db.query("SELECT * FROM carpool_history ORDER BY created_at DESC")
    activity = db.query("SELECT * FROM activity_log ORDER BY created_at DESC")

    user_by_email = {u["user_email"]: u for u in users}

    def person(email: str) -> dict[str, str]:
        u = user_by_email.get(email, {})
        first = str(u.get("first_name") or "").strip() or email.split("@")[0]
        initial = str(u.get("last_initial") or "").strip()[:1]
        return {
            "name": f"{first} {initial}." if initial else first,
            "email": email,
            "phone": str(u.get("phone") or ""),
        }

    members_by_carpool: dict[int, list[dict[str, Any]]] = {}
    for m in members:
        members_by_carpool.setdefault(m["carpool_id"], []).append(m)
    active_carpools = []
    flights_by_carpool = _flights_for(carpools)
    for c in carpools:
        roster = members_by_carpool.get(c["id"], [])
        seats = int(c.get("seats_available") or 0)
        active_carpools.append({
            "entry": c,
            "date": _display_date(c.get("requested_flight_date")),
            "trip": _trip_label(c),
            "flights": flights_by_carpool.get(c["id"], []),
            "direction": _direction(c),
            "leave_time": _display_time(c.get("planned_departure_time")) or "—",
            "created": _fmt_local(c.get("created_at")),
            "seats": seats,
            "seats_left": max(0, seats - len(roster)),
            "members": [
                {**person(m["user_email"]),
                 "is_organizer": m["user_email"] == c.get("creator_email"),
                 "joined": _fmt_local(m.get("joined_at"))}
                for m in roster
            ],
        })
    riders = {m["user_email"] for m in members}

    range_activity = [a for a in activity if in_range(a.get("created_at"))]
    people_by_day: dict[Any, set[str]] = {}
    for a in range_activity:
        dt = _local_dt(a.get("created_at"))
        if dt and a.get("user_email"):
            people_by_day.setdefault(dt.date(), set()).add(a["user_email"])
    joins = [a for a in range_activity if a["event"] == "joined"]
    invite_joins = sum(1 for a in joins if "invite" in str(a.get("detail") or ""))
    active_people = {a["user_email"] for a in range_activity if a.get("user_email")}

    created_in_range = [h for h in history if in_range(h.get("created_at"))]
    closed_in_range = [h for h in history if h.get("outcome") and in_range(h.get("closed_at"))]
    departed = [h for h in closed_in_range if h["outcome"] == "departed"]
    matched = [h for h in departed if int(h.get("peak_members") or 0) >= 2]
    avg_group = (sum(int(h.get("peak_members") or 0) for h in departed) / len(departed)) if departed else 0

    airports: dict[str, int] = {}
    for h in created_in_range:
        code = str(h.get("airport_code") or "?")
        airports[code] = airports.get(code, 0) + 1
    top_airports = [{"label": k, "value": v} for k, v in sorted(airports.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]

    outcome_counts = [
        {"key": "matched", "label": "Departed with 2+ riders", "value": len(matched)},
        {"key": "solo", "label": "Departed with 1 rider", "value": len(departed) - len(matched)},
        {"key": "cancelled", "label": "Disbanded, abandoned or deleted",
         "value": len(closed_in_range) - len(departed)},
    ]

    upcoming: dict[str, int] = {}
    for c in carpools:
        try:
            d = datetime.strptime(str(c.get("requested_flight_date") or ""), "%m-%d-%Y").date()
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=13):
            upcoming[d.isoformat()] = upcoming.get(d.isoformat(), 0) + 1
    upcoming_series = []
    for i in range(14):
        d = today + timedelta(days=i)
        upcoming_series.append({"label": d.strftime("%m/%d"), "value": upcoming.get(d.isoformat(), 0)})

    carpools_per_user: dict[str, int] = {}
    for m in members:
        carpools_per_user[m["user_email"]] = carpools_per_user.get(m["user_email"], 0) + 1
    # Joins counting toward the rolling limit right now, per student.
    now_utc = _now_utc()
    window_rows = db.query(
        f"SELECT user_email, joined_at FROM join_log WHERE joined_at > {db.placeholder}",
        ((now_utc - JOIN_LIMIT_WINDOW).isoformat(),),
    )
    window_joins: dict[str, list[datetime]] = {}
    for r in window_rows:
        try:
            joined = datetime.fromisoformat(str(r["joined_at"]))
        except ValueError:
            continue
        window_joins.setdefault(r["user_email"], []).append(joined)

    def join_limit(email: str) -> dict[str, Any]:
        recent = sorted(window_joins.get(email, []))
        used = len(recent)
        blocked = used >= JOIN_LIMIT_COUNT
        return {
            "used": used,
            "limit": JOIN_LIMIT_COUNT,
            "blocked": blocked,
            "resets": _fmt_local((recent[0] + JOIN_LIMIT_WINDOW).isoformat()) if blocked else "",
        }

    # Suspended accounts.
    banned_rows = db.query("SELECT * FROM banned_users ORDER BY banned_at DESC")
    banned_emails = {b["user_email"] for b in banned_rows}
    banned = [
        {**person(b["user_email"]), "reason": b.get("reason") or "", "since": _fmt_local(b.get("banned_at"))}
        for b in banned_rows
    ]

    joins_per_user: dict[str, int] = {}
    for a in activity:
        if a["event"] == "joined":
            joins_per_user[a["user_email"]] = joins_per_user.get(a["user_email"], 0) + 1
    students = [
        {**person(u["user_email"]),
         "signed_up": _fmt_local(u.get("created_at")),
         "last_login": _fmt_local(u.get("last_login_at")) if u.get("last_login_at") else "—",
         "active_carpools": carpools_per_user.get(u["user_email"], 0),
         "terms": (f"v{u.get('terms_version')} · {_fmt_local(u.get('terms_accepted_at'))}" if u.get("terms_version") else "—"),
         "joins": joins_per_user.get(u["user_email"], 0),
         "join_limit": join_limit(u["user_email"]),
         "banned": u["user_email"] in banned_emails}
        for u in users
    ]

    past = [
        {"id": h["carpool_id"],
         "flight_code": h.get("flight_code") or "",
         "airport_code": h.get("airport_code") or "",
         "trip": _trip_label(h),
         "direction": _direction(h),
         "date": _display_date(h.get("flight_date")),
         "seats": h.get("seats") or 0,
         "peak": h.get("peak_members") or 0,
         "outcome": OUTCOME_LABELS.get(h.get("outcome"), h.get("outcome")),
         "outcome_key": h.get("outcome"),
         "created": _fmt_local(h.get("created_at")),
         "closed": _fmt_local(h.get("closed_at"))}
        for h in history if h.get("outcome")
    ][:200]

    feed = []
    for a in range_activity[:150]:
        who = person(a["user_email"])["name"] if a.get("user_email") else ""
        feed.append({
            "when": _fmt_local(a.get("created_at")),
            "event": a["event"],
            "who": who,
            "email": a.get("user_email") or "",
            "what": EVENT_LABELS.get(a["event"], a["event"]),
            "carpool_id": a.get("carpool_id") or "",
            "detail": a.get("detail") or "",
        })

    return {
        "days": days,
        "ranges": ADMIN_RANGES,
        "range_label": f"{start.strftime('%m/%d/%Y')} – {today.strftime('%m/%d/%Y')}",
        "tracking_since": _fmt_local(min((a["created_at"] for a in activity), default="")) if activity else "",
        "kpis": {
            "students": len(users),
            "new_students": sum(1 for u in users if in_range(u.get("created_at"))),
            "active_students": len(active_people),
            "active_carpools": len(carpools),
            "riders": len(riders),
            "created": len(created_in_range),
            "joins": len(joins),
            "invite_joins": invite_joins,
            "departed": len(departed),
            "match_rate": round(100 * len(matched) / len(departed)) if departed else None,
            "avg_group": round(avg_group, 1) if departed else None,
        },
        "charts": {
            "created": per_day([h.get("created_at") for h in history]),
            "signups": per_day([u.get("created_at") for u in users]),
            "joins": per_day([a.get("created_at") for a in activity if a["event"] == "joined"]),
            "active_people": [{"label": d.strftime("%m/%d"), "value": len(people_by_day.get(d, ()))} for d in day_keys],
            "airports": top_airports,
            "upcoming": upcoming_series,
            "outcomes": outcome_counts,
            "directions": [
                {"key": "to", "label": "UCR → Airport",
                 "value": sum(1 for h in created_in_range if _direction(h) == "to_airport")},
                {"key": "from", "label": "Airport → UCR",
                 "value": sum(1 for h in created_in_range if _direction(h) == "from_airport")},
            ],
        },
        "active_carpools": active_carpools,
        "students": students,
        "banned": banned,
        "past": past,
        "feed": feed,
    }


@app.get("/admin")
def admin_panel() -> Any:
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    if days not in ADMIN_RANGES:
        days = 30
    _cleanup_expired_entries()
    return render_template("admin.html", dash=_admin_dashboard(days))


@app.post("/admin/delete-all")
def admin_delete_all() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    _clear_all_data()
    return redirect(url_for("admin_panel"))


@app.post("/admin/reset-join-limit")
def admin_reset_join_limit() -> Any:
    """Clear a student's rolling join count so they can join again right away."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = str(request.form.get("email", "")).strip()
    if email:
        db.execute(f"DELETE FROM join_log WHERE user_email = {db.placeholder}", (email,))
    return redirect(url_for("admin_panel") + "#students")


@app.post("/admin/ban")
def admin_ban_user() -> Any:
    """Suspend an account: signs them out, blocks sign-in and removes them
    from their carpools."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = str(request.form.get("email", "")).strip()
    reason = str(request.form.get("reason", "")).strip()[:MAX_BAN_REASON_LENGTH]
    if email:
        p = db.placeholder
        if not _is_banned(email):
            db.execute(
                f"INSERT INTO banned_users (user_email, reason, banned_at) VALUES ({p}, {p}, {p})",
                (email, reason, _now_utc().isoformat()),
            )
        _remove_user_from_carpools(email, "")
        _log_event("banned", email, 0, reason)
    return redirect(url_for("admin_panel") + "#students")


@app.post("/admin/unban")
def admin_unban_user() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = str(request.form.get("email", "")).strip()
    if email:
        db.execute(f"DELETE FROM banned_users WHERE user_email = {db.placeholder}", (email,))
        _log_event("unbanned", email)
    return redirect(url_for("admin_panel") + "#students")


@app.post("/admin/delete-user")
def admin_delete_user() -> Any:
    """Honor a deletion request (see docs/data-requests.md)."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = str(request.form.get("email", "")).strip()
    if email:
        _delete_user_data(email)
        _log_event("user_deleted", "", 0, "deletion request")
    return redirect(url_for("admin_panel") + "#students")


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (entry_id,))
    if rows:
        _history_close(rows[0], "admin_deleted")
        _log_event("admin_deleted", "", entry_id)
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


@app.get("/admin/logout")
def admin_logout_link() -> Any:
    return redirect(url_for("admin_login_page"))


@app.post("/admin/logout")
def admin_logout() -> Any:
    for key in ADMIN_SESSION_KEYS:
        session.pop(key, None)
    resp = redirect(url_for("admin_login_page"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.after_request
def _add_response_headers(response: Any) -> Any:
    """Security headers everywhere; no caching where stale state causes problems."""
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # Basic hardening: no framing (clickjacking), no MIME sniffing.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.get("/health")
def health_check() -> Any:
    """Liveness check. Database details are only shown to a logged-in admin."""
    if not _require_admin():
        return jsonify({"status": "ok"})
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
    # Bind to localhost only: the Werkzeug debugger allows code execution.
    app.run(host="127.0.0.1", port=8000, debug=_truthy_env("FLASK_DEBUG", True))
