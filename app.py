from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
import sqlite3
import ssl
from typing import Any

import traceback

# Load .env file for local development (silently ignored if not present or
# if python-dotenv is not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, flash, g, has_request_context, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
_flask_secret = os.getenv("FLASK_SECRET_KEY")
if not _flask_secret:
    raise RuntimeError("FLASK_SECRET_KEY environment variable must be set.")
app.secret_key = _flask_secret
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
csrf = CSRFProtect(app)
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 hour
app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken', 'X-CSRF-Token']
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure flag is set only when running under HTTPS (Vercel always uses HTTPS)
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("VERCEL") or os.getenv("SESSION_COOKIE_SECURE"))

# ---------------------------------------------------------------------------
# Stripe configuration
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY", "")    # $0.99/mo
STRIPE_PRICE_ANNUAL = os.getenv("STRIPE_PRICE_ANNUAL", "")      # $8.28/yr
STRIPE_PRICE_SEARCH_PACK = os.getenv("STRIPE_PRICE_SEARCH_PACK", "")  # $2.99 one-time

# ---------------------------------------------------------------------------
# Optional SDK initialization (lazy-loaded to keep cold starts lighter)
# ---------------------------------------------------------------------------
_firebase_app = None
_fb_auth = None
_fb_sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
_firebase_init_attempted = False
_stripe_lib = None
_stripe_import_attempted = False


def _get_stripe_lib() -> Any | None:
    """Import Stripe only for routes that actually use it."""
    global _stripe_lib, _stripe_import_attempted
    if not _stripe_import_attempted:
        _stripe_import_attempted = True
        try:
            import stripe as stripe_module
            _stripe_lib = stripe_module
        except ImportError:
            _stripe_lib = None
    return _stripe_lib


def _get_firebase_auth() -> Any | None:
    """Initialize Firebase Admin only when token verification is needed."""
    global _firebase_app, _fb_auth, _firebase_init_attempted
    if _firebase_init_attempted:
        return _fb_auth if _firebase_app else None

    _firebase_init_attempted = True
    if not _fb_sa_json:
        app.logger.warning(
            "FIREBASE_SERVICE_ACCOUNT_JSON not set. Firebase token verification is disabled."
        )
        return None

    try:
        import json as _json
        import firebase_admin
        from firebase_admin import auth as fb_auth_module, credentials as fb_credentials

        _sa_dict = _json.loads(_fb_sa_json)
        _fb_cred = fb_credentials.Certificate(_sa_dict)
        _firebase_app = firebase_admin.initialize_app(_fb_cred)
        _fb_auth = fb_auth_module
    except Exception as exc:
        app.logger.warning(
            "Firebase Admin SDK init failed: %s. Token verification disabled.",
            exc,
        )
        _firebase_app = None
        _fb_auth = None

    return _fb_auth if _firebase_app else None


@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf)


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

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
# Accept a pre-computed hash via env var (most secure), or a plaintext password
# via ADMIN_PASSWORD env var. No default — must be configured explicitly.
_admin_pw_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
if not _admin_pw_hash:
    _admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not _admin_pw:
        raise RuntimeError(
            "ADMIN_PASSWORD or ADMIN_PASSWORD_HASH environment variable must be set"
        )
    _admin_pw_hash = generate_password_hash(_admin_pw)
ADMIN_PASSWORD_HASH = _admin_pw_hash
FLIGHT_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$")
PHONE_PATTERN = re.compile(r"^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$")
NAME_PATTERN = re.compile(r"^[A-Za-z \-']+$")


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


def _resolve_database_path() -> str:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "/tmp/carpool.db"
    return "carpool.db"


DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()
DATABASE_PATH = _resolve_database_path()
AUTO_INIT_DB = os.getenv(
    "AUTO_INIT_DB",
    "1" if DB_ENGINE == "sqlite" and not os.getenv("VERCEL") else "0",
).lower() in ("1", "true", "yes")
LIGHTWEIGHT_PUBLIC_PATHS = {"/", "/landing", "/login"}

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
        self._schema_needs_reinit = False  # set True when falling back to SQLite

    def _activate_sqlite_fallback(self, reason: str = "") -> None:
        """Switch to SQLite fallback. Retries MySQL after 60 s of downtime."""
        import time
        if not self._mysql_failed:
            app.logger.warning(f"Switching to SQLite fallback. {reason}")
        self._mysql_failed = True
        self._mysql_failed_at = time.time()
        self.placeholder = "?"
        self._schema_needs_reinit = True  # SQLite schema must be (re-)initialized
        # Discard any broken MySQL connection — g is only available in a request context
        if has_request_context():
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
        """Execute a statement and return the number of rows affected (rowcount)."""
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(sql, params)
            rowcount = cur.rowcount
            conn.commit()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL execute failed: {exc}")
                conn = self._get_sqlite_conn()
                cur = conn.cursor()
                cur.execute(sql.replace("%s", "?"), params)
                rowcount = cur.rowcount
                conn.commit()
                cur.close()
            else:
                raise
        return int(rowcount if rowcount is not None else 0)

    def execute_insert(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Execute an INSERT statement and return the last inserted row ID."""
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(sql, params)
            last_id = getattr(cur, "lastrowid", 0)
            conn.commit()
            cur.close()
        except Exception as exc:
            if self.engine == "mysql" and self._is_mysql_conn_error(exc):
                self._activate_sqlite_fallback(f"MySQL execute_insert failed: {exc}")
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

        # Create subscriptions table for Stripe billing
        if use_mysql:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_email VARCHAR(255) NOT NULL UNIQUE,
                    stripe_customer_id VARCHAR(255) NOT NULL DEFAULT '',
                    stripe_subscription_id VARCHAR(255) NOT NULL DEFAULT '',
                    plan_type VARCHAR(32) NOT NULL DEFAULT '',
                    sub_status VARCHAR(32) NOT NULL DEFAULT '',
                    current_period_end VARCHAR(64) NOT NULL DEFAULT '',
                    search_credits INT NOT NULL DEFAULT 0,
                    created_at VARCHAR(64) NOT NULL DEFAULT '',
                    updated_at VARCHAR(64) NOT NULL DEFAULT ''
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL UNIQUE,
                    stripe_customer_id TEXT NOT NULL DEFAULT '',
                    stripe_subscription_id TEXT NOT NULL DEFAULT '',
                    plan_type TEXT NOT NULL DEFAULT '',
                    sub_status TEXT NOT NULL DEFAULT '',
                    current_period_end TEXT NOT NULL DEFAULT '',
                    search_credits INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
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
                # Idempotency: track last processed checkout session to prevent double-credits
                cur.execute("SHOW COLUMNS FROM subscriptions LIKE 'last_checkout_session_id'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE subscriptions ADD COLUMN last_checkout_session_id VARCHAR(255) NOT NULL DEFAULT ''")
                cur.execute("SHOW COLUMNS FROM users LIKE 'is_banned'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE users ADD COLUMN is_banned INT NOT NULL DEFAULT 0")
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
                cur.execute("PRAGMA table_info(subscriptions)")
                sub_cols = [row[1] for row in cur.fetchall()]
                if "last_checkout_session_id" not in sub_cols:
                    cur.execute("ALTER TABLE subscriptions ADD COLUMN last_checkout_session_id TEXT NOT NULL DEFAULT ''")
                cur.execute("PRAGMA table_info(users)")
                user_cols = [row[1] for row in cur.fetchall()]
                if "is_banned" not in user_cols:
                    cur.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        finally:
            cur.close()


db = DBAdapter()


# ---------------------------------------------------------------------------
# Subscription access control
# ---------------------------------------------------------------------------

def _get_plan_type_from_stripe_sub(sub: Any) -> str:
    """Derive 'monthly' or 'annual' from a Stripe Subscription object."""
    try:
        interval = sub["items"]["data"][0]["price"]["recurring"]["interval"]
        return "annual" if interval == "year" else "monthly"
    except Exception:
        return "monthly"


def _get_period_end_ts(sub: Any) -> int | None:
    """Extract current_period_end timestamp from a Stripe Subscription object.
    Handles both older API (top-level) and newer API (per-item) structures."""
    ts = sub.get("current_period_end")
    if not ts:
        try:
            ts = sub["items"]["data"][0].get("current_period_end")
        except Exception:
            pass
    return int(ts) if ts else None


def get_user_access(user_email: str) -> dict[str, Any]:
    """Return the user's current subscription tier and access rights.

    Checks in order:
      1. 30-day free trial (from users.created_at)
      2. Active recurring subscription (monthly / annual)
      3. Search credit pack (one-time $2.99 purchase)
    """
    # 1. Check 30-day trial
    try:
        user_rows = db.query(
            f"SELECT created_at FROM users WHERE user_email = {db.placeholder}",
            (user_email,),
        )
        if user_rows and user_rows[0].get("created_at"):
            created_str = user_rows[0]["created_at"]
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            trial_end = created_dt + timedelta(days=30)
            if _now_utc() < trial_end:
                days_left = max(1, (trial_end - _now_utc()).days + 1)
                # Check if user already has an active paid subscription running concurrently
                has_paid_sub = False
                try:
                    sub_rows = db.query(
                        f"SELECT sub_status FROM subscriptions WHERE user_email = {db.placeholder}",
                        (user_email,),
                    )
                    if sub_rows and sub_rows[0].get("sub_status") == "active":
                        has_paid_sub = True
                except Exception:
                    pass
                if not has_paid_sub:
                    return {
                        "tier": "trial",
                        "can_search": True,
                        "can_create": True,
                        "can_join": True,
                        "trial_ends_at": trial_end.isoformat(),
                        "trial_days_left": days_left,
                        "has_active_subscription": False,
                    }
                # has_paid_sub is True — fall through to return actual subscription tier
    except Exception:
        pass

    # 2. Check active recurring subscription or search credits
    try:
        sub_rows = db.query(
            f"SELECT * FROM subscriptions WHERE user_email = {db.placeholder}",
            (user_email,),
        )
        if sub_rows:
            s = sub_rows[0]
            if s.get("sub_status") == "active" and s.get("current_period_end"):
                plan = s.get("plan_type") or "monthly"
                period_end_str = s["current_period_end"]
                still_valid = False
                try:
                    period_end = datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
                    if period_end.tzinfo is None:
                        period_end = period_end.replace(tzinfo=timezone.utc)
                    still_valid = _now_utc() < period_end
                except Exception:
                    # Cannot parse date — treat as expired rather than giving free access
                    still_valid = False
                if still_valid:
                    return {
                        "tier": plan,
                        "can_search": True,
                        "can_create": True,
                        "can_join": True,
                        "current_period_end": period_end_str,
                    }
            # 3. Check search credits (one-time pack)
            credits = int(s.get("search_credits", 0) or 0)
            if credits > 0:
                return {
                    "tier": "search_pack",
                    "can_search": True,
                    "can_create": False,
                    "can_join": True,
                    "search_credits": credits,
                }
    except Exception:
        pass

    return {
        "tier": "none",
        "can_search": False,
        "can_create": False,
        "can_join": False,
    }


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


def _should_skip_db_bootstrap(path: str) -> bool:
    return path in LIGHTWEIGHT_PUBLIC_PATHS or path.startswith("/static/") or path.startswith("/docs/")


@app.before_request
def _ensure_db() -> None:
    """Auto-bootstrap local SQLite and SQLite fallback without penalizing every cold start."""
    global _db_initialized
    if _db_initialized and not db._schema_needs_reinit:
        return
    if not AUTO_INIT_DB and not db._schema_needs_reinit and _should_skip_db_bootstrap(request.path):
        return
    db._schema_needs_reinit = False
    try:
        db.init_schema()
        db.ensure_columns()
        _db_initialized = True
    except Exception:
        pass


@app.before_request
def enforce_ban_on_api():
    """Block banned users from all /api/ endpoints."""
    if request.path.startswith("/api/"):
        email = session.get("user_email")
        if email and _is_user_banned(email):
            session.clear()
            return jsonify({"error": "Your account has been suspended. Contact support."}), 403


@app.errorhandler(Exception)
def handle_exception(e: Exception) -> Any:
    """Return JSON errors for API routes, HTML for pages. Never leak internals."""
    tb = traceback.format_exc()
    app.logger.error(f"Unhandled exception: {e}\n{tb}")
    if request.path.startswith("/api/"):
        return jsonify({"error": "An internal server error occurred. Please try again."}), 500
    return "<h1>Internal Server Error</h1><p>Something went wrong. Please try again.</p>", 500


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


def _is_user_banned(email: str) -> bool:
    """Returns True if the user is banned."""
    if not email:
        return False
    p = db.placeholder
    try:
        rows = db.query(f"SELECT is_banned FROM users WHERE user_email = {p}", (email,))
        return bool(rows and rows[0].get("is_banned"))
    except Exception:
        return False


def _require_user_login() -> bool:
    """Check if a regular user is logged in via Firebase."""
    email = session.get("user_email")
    if not email:
        return False
    p = db.placeholder
    try:
        rows = db.query(f"SELECT is_banned FROM users WHERE user_email = {p}", (email,))
        if rows and rows[0].get("is_banned"):
            session.clear()
            return False
    except Exception:
        pass
    return True


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
    access = get_user_access(session["user_email"])
    if not access.get("can_create"):
        return redirect(url_for("pricing_page"))
    return render_template("create_a_carpool.html", **_user_context())


@app.get("/add-flight-details")
def add_flight_details_redirect() -> Any:
    """Redirect old URL to new one for backwards compatibility."""
    return redirect(url_for("create_a_carpool_page"), code=301)


@app.get("/find-a-carpool")
def find_a_carpool_page() -> Any:
    if not _require_user_login():
        return redirect(url_for("login_page"))
    access = get_user_access(session["user_email"])
    if not access.get("can_search"):
        return redirect(url_for("pricing_page"))
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
@csrf.exempt
@limiter.limit("20 per minute")
def firebase_callback() -> Any:
    """Receive Firebase ID token from client, verify it server-side, set session."""
    data = request.get_json(silent=True) or {}
    id_token = data.get("idToken", "")
    firebase_auth = _get_firebase_auth()
    if firebase_auth:
        try:
            decoded = firebase_auth.verify_id_token(id_token)
            email = decoded.get("email", "").strip().lower()
            name = decoded.get("name", data.get("name", "")).strip()
            uid = decoded.get("uid", "").strip()
        except Exception:
            return jsonify({"error": "Invalid or expired authentication token."}), 401
    else:
        # Dev fallback — not safe for production
        print("WARNING: Firebase token not verified (no service account configured).")
        email = str(data.get("email", "")).strip().lower()
        name = str(data.get("name", "")).strip()
        uid = str(data.get("uid", "")).strip()

    if not email or not uid:
        return jsonify({"error": "Missing authentication data"}), 400

    if not email.endswith("@ucr.edu"):
        return jsonify({"error": "Only @ucr.edu accounts are allowed", "code": "ucr_only"}), 403

    p = db.placeholder
    try:
        ban_rows = db.query(f"SELECT is_banned FROM users WHERE user_email = {p}", (email,))
        if ban_rows and ban_rows[0].get("is_banned"):
            return jsonify({"error": "Logged in with a banned account. Contact support.", "code": "banned_account"}), 403
    except Exception:
        pass

    session.permanent = True
    session["user_email"] = email
    session["user_name"] = name
    session["user_uid"] = uid

    now_iso = _now_utc().isoformat()
    parts = name.strip().split() if name else []
    first_name = parts[0] if parts else ""
    last_initial = parts[-1][0].upper() if len(parts) > 1 and parts[-1] else ""

    # Always create the users row (needed for trial tracking via created_at)
    try:
        existing = db.query(f"SELECT user_email FROM users WHERE user_email = {p}", (email,))
        if not existing:
            db.execute(
                f"INSERT INTO users (user_email, first_name, last_initial, phone, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                (email, first_name, last_initial, "", now_iso),
            )
            # Welcome notification: inform new user of their 30-day free trial
            trial_end_date = (_now_utc() + timedelta(days=30)).strftime("%B %d, %Y")
            try:
                db.execute(
                    f"INSERT INTO notifications (user_email, message, created_at, dismissed) VALUES ({p}, {p}, {p}, 0)",
                    (email, f"🎉 Welcome to Campus2Air! Your free 30-day trial has started and will end on {trial_end_date}. Enjoy unlimited searches and carpool creation during your trial.", now_iso),
                )
            except Exception:
                pass
        elif name:
            # Only update name fields if a name was provided (don't blank out existing)
            db.execute(
                f"UPDATE users SET first_name = {p}, last_initial = {p} WHERE user_email = {p}",
                (first_name, last_initial, email),
            )
    except Exception:
        pass

    # Always ensure subscription row exists — required for trial and billing tracking
    try:
        existing_sub = db.query(
            f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,)
        )
        if not existing_sub:
            db.execute(
                f"INSERT INTO subscriptions (user_email, search_credits, created_at, updated_at) VALUES ({p}, 0, {p}, {p})",
                (email, now_iso, now_iso),
            )
    except Exception:
        pass

    return jsonify({"ok": True, "redirect": url_for("start_now_page")})


@app.get("/auth/logout")
def user_logout() -> Any:
    session.clear()
    return redirect(url_for("landing"))


@app.get("/search")
def search_page() -> Any:
    return redirect(url_for("find_a_carpool_page"), code=302)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/carpools")
@limiter.limit("10 per minute")
def create_carpool() -> Any:
    try:
        return _create_carpool_inner()
    except Exception as e:
        app.logger.error(f"create_carpool error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


def _create_carpool_inner() -> Any:
    _cleanup_expired_entries()

    # Subscription gate: only monthly/annual/trial users can create carpools
    creator_email = session.get("user_email", "")
    if not creator_email:
        return jsonify({"error": "Login required"}), 401
    access = get_user_access(creator_email)
    if not access.get("can_create"):
        return jsonify({
            "error": "subscription_required",
            "message": "Creating a carpool requires a Monthly or Annual subscription.",
            "tier_needed": "monthly",
        }), 403

    data = request.get_json(silent=True) or request.form.to_dict()

    required = ["phone", "flight_code", "airport_code"]
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if not str(data.get("departure_date") or data.get("flight_date") or "").strip():
        missing.append("departure_date")
    if missing:
        return jsonify({"error": "Missing required fields", "missing": missing}), 400

    # Derive name from the Google login stored in session
    raw_name = session.get("user_name", "")
    name_parts = raw_name.strip().split() if raw_name else []
    first_name = name_parts[0].title() if name_parts else ""
    last_initial = name_parts[-1][0].upper() if len(name_parts) > 1 and name_parts[-1] else ""
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
    today = datetime.now(timezone.utc).date()
    if parsed_flight_date.date() < today:
        return jsonify({"error": "Departure date cannot be in the past."}), 400
    flight_date_user = _to_user_flight_date(parsed_flight_date)

    airport_name, airport_location = _resolve_airport(airport_code)
    created_at = _now_utc().isoformat()

    # Expire at 11:59 PM UTC the day AFTER the departure date.
    # +1 day buffer prevents users in UTC-offset timezones (e.g. California, UTC-8)
    # from having their carpool instantly cleaned up when their local "today"
    # is already "yesterday" in UTC.
    expires_at = (parsed_flight_date + timedelta(days=1)).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).isoformat()

    try:
        _seats_available_val = max(1, min(7, int(data.get("seats_available", 4) or 4)))
    except (ValueError, TypeError):
        return jsonify({"error": "seats_available must be a number between 1 and 7."}), 400

    destination_airport = str(data.get("destination_airport", "")).strip().upper()[:3]
    planned_departure_time = str(data.get("planned_departure_time", "")).strip()
    creator_email = session.get("user_email", "")

    p = db.placeholder
    last_id = db.execute_insert(
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
            _seats_available_val,
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
@limiter.limit("30 per minute")
def search_carpools() -> Any:
    _cleanup_expired_entries()

    # Subscription gate: require login + active access to search
    search_user = session.get("user_email", "")
    if not search_user:
        return jsonify({"error": "Login required", "subscription_required": True}), 401
    access = get_user_access(search_user)
    if not access.get("can_search"):
        return jsonify({
            "error": "subscription_required",
            "message": "Searching carpools requires an active subscription or Search Pack.",
            "tier_needed": "search_pack",
        }), 403

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

    # For search_pack: atomically consume credit BEFORE running the search
    credit_consumed = False
    credit_refunded = False

    def refund_search_credit() -> None:
        nonlocal credit_refunded
        if not credit_consumed or credit_refunded:
            return
        db.execute(
            f"UPDATE subscriptions SET search_credits = search_credits + 1, updated_at = {p} WHERE user_email = {p}",
            (_now_utc().isoformat(), search_user),
        )
        credit_refunded = True

    if access.get("tier") == "search_pack":
        affected = db.execute(
            f"UPDATE subscriptions SET search_credits = search_credits - 1, updated_at = {p} WHERE user_email = {p} AND search_credits > 0",
            (_now_utc().isoformat(), search_user),
        )
        if not affected:  # 0 rows updated = no credits left
            return jsonify({"error": "No search credits remaining.", "upgrade": True}), 403
        credit_consumed = True

    try:
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

        # If search_pack and no results found, refund the credit
        if credit_consumed and not results:
            refund_search_credit()

        return jsonify({"count": len(results), "results": results})
    except Exception as e:
        if credit_consumed and not credit_refunded:
            try:
                refund_search_credit()
            except Exception as refund_error:
                app.logger.error(f"search_carpools credit refund failed: {refund_error}")
        app.logger.error(f"search_carpools error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.get("/api/carpools/<int:entry_id>")
def carpool_details(entry_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    _cleanup_expired_entries()
    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (entry_id,))
    if not rows:
        return jsonify({"error": "Not found"}), 404
    member_rows = db.query(
        f"SELECT 1 FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
        (entry_id, email),
    )
    is_member = bool(member_rows)
    return jsonify({"entry": _serialize_entry(rows[0], include_phone=is_member)})


@app.post("/api/carpools/<int:carpool_id>/join")
def join_party(carpool_id: int) -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401

    # Subscription gate: need active access to join
    join_access = get_user_access(email)
    if not join_access.get("can_join"):
        return jsonify({
            "error": "subscription_required",
            "message": "Joining a carpool requires an active subscription or Search Pack.",
            "tier_needed": "search_pack",
        }), 403

    p = db.placeholder
    rows = db.query(f"SELECT * FROM carpools WHERE id = {p}", (carpool_id,))
    if not rows:
        return jsonify({"error": "Carpool not found"}), 404

    carpool = rows[0]

    # Reject joins on expired carpools (join_party doesn't call _cleanup_expired_entries)
    expires_at = carpool.get("expires_at", "")
    if expires_at and expires_at <= _now_utc().isoformat():
        return jsonify({"error": "This carpool has expired"}), 410

    max_members = int(carpool.get("seats_available", 3))

    # Atomically check both "already a member" and "seat cap" in one conditional INSERT.
    # This eliminates the TOCTOU race where two simultaneous joins could both pass the
    # count check and both insert, exceeding the seat cap.
    joined_at = _now_utc().isoformat()
    rows_inserted = db.execute(
        f"""INSERT INTO party_members (carpool_id, user_email, joined_at)
            SELECT {p}, {p}, {p}
            WHERE (SELECT COUNT(*) FROM party_members WHERE carpool_id = {p}) < {p}
            AND NOT EXISTS (
                SELECT 1 FROM party_members WHERE carpool_id = {p} AND user_email = {p}
            )""",
        (carpool_id, email, joined_at, carpool_id, max_members, carpool_id, email),
    )

    if not rows_inserted:
        # Distinguish: already a member vs carpool full
        existing = db.query(
            f"SELECT 1 FROM party_members WHERE carpool_id = {p} AND user_email = {p}",
            (carpool_id, email),
        )
        if existing:
            return jsonify({"error": "Already in this carpool"}), 409
        return jsonify({"error": "This carpool is full"}), 409

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

    # Fetch all current members (excluding the joiner) to send notifications
    existing_members = db.query(
        f"SELECT user_email FROM party_members WHERE carpool_id = {p} AND user_email != {p}",
        (carpool_id, email),
    )

    # Notify creator and all existing members that someone joined
    joiner_name = session.get("user_name", email.split("@")[0])
    flight_code = carpool.get("flight_code", "")
    flight_date = carpool.get("requested_flight_date", "")
    msg = f"{joiner_name} joined your carpool for {flight_code} on {flight_date}."
    creator_email = carpool.get("creator_email", "")
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
        db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))
        return jsonify({"ok": True, "message": "You left and the carpool was disbanded because no members remained."})

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
    profile = db.query(
        f"SELECT first_name, last_initial, phone FROM users WHERE user_email = {p}",
        (new_owner_email,),
    )
    if profile:
        fn = str(profile[0].get("first_name") or "").strip() or new_owner_email.split("@")[0]
        li = str(profile[0].get("last_initial") or "").strip()[:1].upper()
        ph = str(profile[0].get("phone") or "").strip()
        db.execute(
            f"UPDATE carpools SET creator_email = {p}, first_name = {p}, last_initial = {p}, phone = {p} WHERE id = {p}",
            (new_owner_email, fn, li, ph, carpool_id),
        )
    else:
        db.execute(
            f"UPDATE carpools SET creator_email = {p} WHERE id = {p}",
            (new_owner_email, carpool_id),
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
        app.logger.error(f"update_user_phone error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500
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
        updates.append(f"planned_departure_time = {p}")
        params.append(str(data["planned_departure_time"]).strip())
        changed.append("departure time")
    if "notes" in data:
        updates.append(f"notes = {p}")
        params.append(str(data["notes"]).strip())
        changed.append("notes")
    if "seats_available" in data:
        try:
            seats = int(data["seats_available"])
            if 1 <= seats <= 7:
                updates.append(f"seats_available = {p}")
                params.append(seats)
                changed.append("seat count")
        except (ValueError, TypeError):
            pass
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
    # Delete all party members and the carpool
    db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (carpool_id,))
    db.execute(f"DELETE FROM carpools WHERE id = {p}", (carpool_id,))
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


@app.get("/api/csrf-token")
@csrf.exempt
def get_csrf_token() -> Any:
    return jsonify({"csrf_token": generate_csrf()})


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.get("/admin/login")
@limiter.limit("10 per minute")
def admin_login_page() -> Any:
    if _require_admin():
        return redirect(url_for("admin_panel"))
    admin_error = request.args.get("error") == "1"
    return render_template("admin_login.html", admin_error=admin_error)


@app.post("/admin/login")
@limiter.limit("10 per minute")
def admin_login() -> Any:
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
        session.clear()  # prevent session fixation
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
    try:
        users = db.query(
            """
            SELECT u.user_email, u.first_name, u.last_initial, u.phone, u.created_at,
                   u.is_banned,
                   s.plan_type, s.sub_status, s.search_credits, s.current_period_end,
                   s.stripe_customer_id, s.stripe_subscription_id
            FROM users u
            LEFT JOIN subscriptions s ON u.user_email = s.user_email
            ORDER BY u.created_at DESC
            """
        )
    except Exception:
        users = []
    total = len(rows)
    unverified = sum(1 for r in rows if r.get("status") == "unverified")
    unique_flights = len({r.get("flight_code", "") for r in rows})
    return render_template("admin.html", entries=rows, total=total,
                           unverified=unverified, unique_flights=unique_flights,
                           users=users)


@app.post("/admin/delete-all")
def admin_delete_all() -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    db.execute("DELETE FROM party_members")
    db.execute("DELETE FROM carpools")
    return redirect(url_for("admin_panel"))


@app.post("/admin/delete/<int:entry_id>")
def admin_delete_entry(entry_id: int) -> Any:
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    p = db.placeholder
    db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (entry_id,))
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


@app.post("/admin/clear-user-subscription")
def admin_clear_user_subscription() -> Any:
    """Dev/test helper: wipe a user's subscription row so they appear fully locked out."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.form.get("email", "").strip()
    if not email:
        return jsonify({"error": "email required"}), 400
    p = db.placeholder
    try:
        db.execute(
            f"UPDATE subscriptions SET stripe_customer_id = '', stripe_subscription_id = '', plan_type = '', sub_status = '', current_period_end = '', search_credits = 0 WHERE user_email = {p}",
            (email,),
        )
    except Exception as e:
        app.logger.error(f"admin_clear_user_subscription error: {e}")
        return jsonify({"ok": False, "error": "An internal error occurred."}), 500
    return jsonify({"ok": True, "email": email})


@app.post("/admin/set-user-created")
def admin_set_user_created() -> Any:
    """Dev/test helper: manually set a user's created_at to expire their trial."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.form.get("email", "").strip()
    created_at = request.form.get("created_at", "").strip()
    if not email or not created_at:
        return jsonify({"error": "email and created_at required"}), 400
    p = db.placeholder
    db.execute(
        f"UPDATE users SET created_at = {p} WHERE user_email = {p}",
        (created_at, email),
    )
    rows = db.query(f"SELECT user_email, created_at FROM users WHERE user_email = {p}", (email,))
    return jsonify({"ok": True, "user": rows[0] if rows else None})


@app.post("/admin/edit-user")
def admin_edit_user() -> Any:
    """Edit a user's profile and/or subscription fields from the admin panel."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.form.get("email", "").strip().lower()
    if not email:
        return redirect(url_for("admin_panel"))

    p = db.placeholder
    now_iso = _now_utc().isoformat()

    # --- User profile fields ---
    first_name = request.form.get("first_name", "").strip().title()
    last_initial = request.form.get("last_initial", "").strip().upper()[:1]
    phone = request.form.get("phone", "").strip()
    created_at = request.form.get("created_at", "").strip()  # trial reset date

    user_updates: list[str] = []
    user_vals: list[Any] = []
    if first_name:
        user_updates.append(f"first_name = {p}")
        user_vals.append(first_name)
    if last_initial:
        user_updates.append(f"last_initial = {p}")
        user_vals.append(last_initial)
    if phone:
        user_updates.append(f"phone = {p}")
        user_vals.append(phone)
    if created_at:
        user_updates.append(f"created_at = {p}")
        user_vals.append(created_at)
    if user_updates:
        user_vals.append(email)
        db.execute(
            f"UPDATE users SET {', '.join(user_updates)} WHERE user_email = {p}",
            tuple(user_vals),
        )

    # --- Subscription fields ---
    plan_type = request.form.get("plan_type", "").strip()
    sub_status = request.form.get("sub_status", "").strip()
    search_credits_raw = request.form.get("search_credits", "").strip()
    current_period_end = request.form.get("current_period_end", "").strip()
    clear_sub = request.form.get("clear_sub", "").strip()  # "1" to wipe subscription

    if clear_sub == "1":
        db.execute(
            f"UPDATE subscriptions SET stripe_customer_id = '', stripe_subscription_id = '', plan_type = '', sub_status = '', current_period_end = '', search_credits = 0, updated_at = {p} WHERE user_email = {p}",
            (now_iso, email),
        )
    else:
        sub_updates: list[str] = []
        sub_vals: list[Any] = []
        if plan_type != "":
            sub_updates.append(f"plan_type = {p}")
            sub_vals.append(plan_type)
        if sub_status != "":
            sub_updates.append(f"sub_status = {p}")
            sub_vals.append(sub_status)
        if search_credits_raw != "":
            try:
                sc_val = int(search_credits_raw)
                if sc_val < 0:
                    flash("Search credits cannot be negative.", "error")
                    return redirect(url_for("admin_panel"))
                sub_updates.append(f"search_credits = {p}")
                sub_vals.append(sc_val)
            except ValueError:
                flash("Search credits must be a valid integer.", "error")
                return redirect(url_for("admin_panel"))
        if current_period_end:
            sub_updates.append(f"current_period_end = {p}")
            sub_vals.append(current_period_end)
        if sub_updates:
            sub_updates.append(f"updated_at = {p}")
            sub_vals.append(now_iso)
            sub_vals.append(email)
            # Upsert: update if exists, insert minimal row otherwise
            existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
            if existing:
                db.execute(
                    f"UPDATE subscriptions SET {', '.join(sub_updates)} WHERE user_email = {p}",
                    tuple(sub_vals),
                )
            else:
                db.execute(
                    f"INSERT INTO subscriptions (user_email, search_credits, created_at, updated_at) VALUES ({p}, 0, {p}, {p})",
                    (email, now_iso, now_iso),
                )
                db.execute(
                    f"UPDATE subscriptions SET {', '.join(sub_updates)} WHERE user_email = {p}",
                    tuple(sub_vals),
                )

    return redirect(url_for("admin_panel"))


@app.post("/admin/reset-user-trial")
def admin_reset_user_trial() -> Any:
    """Reset a user to a fresh 30-day trial and wipe their subscription."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.form.get("email", "").strip().lower()
    if not email:
        return redirect(url_for("admin_panel"))
    p = db.placeholder
    now_iso = _now_utc().isoformat()
    db.execute(f"UPDATE users SET created_at = {p} WHERE user_email = {p}", (now_iso, email))
    db.execute(
        f"UPDATE subscriptions SET stripe_subscription_id = '', plan_type = '', sub_status = '', current_period_end = '', search_credits = 0, updated_at = {p} WHERE user_email = {p}",
        (now_iso, email),
    )
    return redirect(url_for("admin_panel"))


@app.post("/admin/grant-subscription")
def admin_grant_subscription() -> Any:
    """Manually grant a user a monthly or annual subscription (bypasses Stripe)."""
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.form.get("email", "").strip().lower()
    plan = request.form.get("plan", "").strip()  # 'monthly', 'annual', or 'search_pack'
    if not email or plan not in ("monthly", "annual", "search_pack"):
        return redirect(url_for("admin_panel"))

    p = db.placeholder
    user_exists = db.query(f"SELECT 1 FROM users WHERE user_email = {p}", (email,))
    if not user_exists:
        flash(f"User not found: {email}", "error")
        return redirect(url_for("admin_panel"))

    now_iso = _now_utc().isoformat()

    if plan == "search_pack":
        existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
        if existing:
            db.execute(
                f"UPDATE subscriptions SET search_credits = search_credits + 3, updated_at = {p} WHERE user_email = {p}",
                (now_iso, email),
            )
        else:
            db.execute(
                f"INSERT INTO subscriptions (user_email, search_credits, created_at, updated_at) VALUES ({p}, 3, {p}, {p})",
                (email, now_iso, now_iso),
            )
    else:
        if plan == "monthly":
            period_end = (_now_utc() + timedelta(days=31)).isoformat()
        else:
            period_end = (_now_utc() + timedelta(days=366)).isoformat()

        existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
        if existing:
            db.execute(
                f"UPDATE subscriptions SET plan_type = {p}, sub_status = 'active', current_period_end = {p}, updated_at = {p} WHERE user_email = {p}",
                (plan, period_end, now_iso, email),
            )
        else:
            db.execute(
                f"INSERT INTO subscriptions (user_email, plan_type, sub_status, current_period_end, search_credits, created_at, updated_at) VALUES ({p}, {p}, 'active', {p}, 0, {p}, {p})",
                (email, plan, period_end, now_iso, now_iso),
            )

    return redirect(url_for("admin_panel"))


@app.route("/admin/ban-user", methods=["POST"])
def admin_ban_user():
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    email = request.form.get("email", "").strip()
    action = request.form.get("action", "ban")
    if not email:
        flash("Email required.", "error")
        return redirect(url_for("admin_panel"))
    p = db.placeholder
    flag = 1 if action == "ban" else 0
    db.execute(f"UPDATE users SET is_banned = {p} WHERE user_email = {p}", (flag, email))
    if flag == 1:
        notify_user(email, "Your account has been suspended by an administrator. Please contact support if you believe this is a mistake.")
    flash(f"User {'banned' if flag else 'unbanned'}: {email}", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete-user", methods=["POST"])
def admin_delete_user():
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    email = request.form.get("email", "").strip()
    if not email:
        flash("Email required.", "error")
        return redirect(url_for("admin_panel"))
    p = db.placeholder
    # Find all carpools created by this user
    carpools = db.query(f"SELECT id FROM carpools WHERE creator_email = {p}", (email,))
    for c in carpools:
        db.execute(f"DELETE FROM party_members WHERE carpool_id = {p}", (c["id"],))
    db.execute(f"DELETE FROM carpools WHERE creator_email = {p}", (email,))
    db.execute(f"DELETE FROM party_members WHERE user_email = {p}", (email,))
    db.execute(f"DELETE FROM subscriptions WHERE user_email = {p}", (email,))
    db.execute(f"DELETE FROM notifications WHERE user_email = {p}", (email,))
    db.execute(f"DELETE FROM users WHERE user_email = {p}", (email,))
    flash(f"User deleted: {email}", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/user-carpools", methods=["GET"])
def admin_user_carpools():
    if not _require_admin():
        return jsonify({"error": "Unauthorized"}), 401
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify({"error": "Email required"}), 400
    p = db.placeholder
    created = db.query(
        f"SELECT id, flight_code, airport_code, requested_flight_date, seats_available, created_at FROM carpools WHERE creator_email = {p}",
        (email,)
    )
    joined_rows = db.query(f"SELECT carpool_id FROM party_members WHERE user_email = {p}", (email,))
    joined = []
    for row in joined_rows:
        c = db.query(f"SELECT id, flight_code, airport_code, requested_flight_date, seats_available, created_at FROM carpools WHERE id = {p}", (row["carpool_id"],))
        if c:
            joined.append(c[0])
    return jsonify({"created": [dict(c) for c in created], "joined": [dict(j) for j in joined]})


@app.route("/admin/revoke-subscription", methods=["POST"])
def admin_revoke_subscription():
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    email = request.form.get("email", "").strip()
    if not email:
        flash("Email required.", "error")
        return redirect(url_for("admin_panel"))
    p = db.placeholder
    now = _now_utc().isoformat()
    db.execute(
        f"UPDATE subscriptions SET sub_status='canceled', stripe_subscription_id='', plan_type='', current_period_end='', updated_at={p} WHERE user_email={p}",
        (now, email)
    )
    flash(f"Subscription revoked for {email}", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/set-user-credits", methods=["POST"])
def admin_set_user_credits():
    if not _require_admin():
        return redirect(url_for("admin_login_page"))
    email = request.form.get("email", "").strip()
    credits_str = request.form.get("credits", "").strip()
    if not email or credits_str == "":
        flash("Email and credits required.", "error")
        return redirect(url_for("admin_panel"))
    try:
        credits = int(credits_str)
    except ValueError:
        flash("Credits must be an integer.", "error")
        return redirect(url_for("admin_panel"))
    if credits < 0:
        flash("Credits cannot be negative.", "error")
        return redirect(url_for("admin_panel"))
    p = db.placeholder
    now = _now_utc().isoformat()
    existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
    if existing:
        db.execute(f"UPDATE subscriptions SET search_credits={p}, updated_at={p} WHERE user_email={p}", (credits, now, email))
    else:
        db.execute(
            f"INSERT INTO subscriptions (user_email, search_credits, updated_at) VALUES ({p},{p},{p})",
            (email, credits, now)
        )
    flash(f"Credits set to {credits} for {email}", "success")
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
    """Debug endpoint to check app status on Vercel. Admin-only."""
    if not _require_admin():
        return jsonify({"status": "ok"}), 200
    actual_engine = "sqlite" if db._mysql_failed else db.engine
    info: dict[str, Any] = {
        "status": "ok",
        "db_engine_configured": DB_ENGINE,
        "db_engine_active": actual_engine,
        "mysql_fallback_active": db._mysql_failed,
        "db_path": DATABASE_PATH if actual_engine == "sqlite" else "(mysql)",
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
# Subscription routes
# ---------------------------------------------------------------------------

@app.get("/pricing")
def pricing_page() -> Any:
    user_email = session.get("user_email", "")
    access = get_user_access(user_email) if user_email else {}
    return render_template(
        "pricing.html",
        user_email=user_email,
        access=access,
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
        price_monthly=STRIPE_PRICE_MONTHLY,
        price_annual=STRIPE_PRICE_ANNUAL,
        price_search_pack=STRIPE_PRICE_SEARCH_PACK,
    )


@app.get("/account")
def account_page() -> Any:
    email = session.get("user_email")
    if not email:
        return redirect(url_for("login_page"))
    access = get_user_access(email)
    p = db.placeholder
    sub_rows = db.query(f"SELECT * FROM subscriptions WHERE user_email = {p}", (email,))
    sub = sub_rows[0] if sub_rows else {}

    # Fetch live Stripe subscription data for upcoming billing panel
    stripe_sub_data = None
    stripe_lib = _get_stripe_lib()
    if stripe_lib and STRIPE_SECRET_KEY and sub.get("stripe_subscription_id"):
        try:
            stripe_lib.api_key = STRIPE_SECRET_KEY
            s = stripe_lib.Subscription.retrieve(sub["stripe_subscription_id"])
            item = s["items"]["data"][0]
            amount = item["price"]["unit_amount"]
            currency = item["price"]["currency"].upper()
            interval = item["price"]["recurring"]["interval"]
            ts = _get_period_end_ts(s)
            period_end = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else _now_utc()
            stripe_sub_data = {
                "cancel_at_period_end": s.get("cancel_at_period_end", False),
                "next_billing_date": period_end.strftime("%B %d, %Y"),
                "amount": f"{currency} {amount / 100:.2f}",
                "interval": interval,
                "status": s.get("status", ""),
            }
        except Exception:
            pass

    return render_template(
        "account.html",
        user_email=email,
        access=access,
        sub=sub,
        stripe_sub_data=stripe_sub_data,
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
        price_monthly=STRIPE_PRICE_MONTHLY,
        price_annual=STRIPE_PRICE_ANNUAL,
    )


@app.get("/subscription/success")
def subscription_success() -> Any:
    return render_template("subscription_success.html", user_email=session.get("user_email", ""))


@app.get("/subscription/cancel-return")
def subscription_cancel_return() -> Any:
    return redirect(url_for("pricing_page"))


@app.post("/api/subscription/sync")
def sync_subscription() -> Any:
    """Called from the success page to sync subscription state directly from Stripe.
    Identifies the user from Stripe customer metadata so it works even if the
    Flask session cookie isn't available after a Stripe redirect."""
    stripe_lib = _get_stripe_lib()
    if not stripe_lib or not STRIPE_SECRET_KEY:
        return jsonify({"ok": True, "skipped": True})

    data = request.get_json(silent=True) or {}
    checkout_session_id = data.get("session_id", "").strip()
    if not checkout_session_id:
        return jsonify({"error": "session_id required"}), 400

    stripe_lib.api_key = STRIPE_SECRET_KEY
    p = db.placeholder
    now_iso = _now_utc().isoformat()

    try:
        cs = stripe_lib.checkout.Session.retrieve(checkout_session_id)
        mode = cs.get("mode")
        customer_id = cs.get("customer", "")

        # Identify user: prefer Stripe customer metadata, fall back to Flask session
        email = ""
        if customer_id:
            try:
                customer = stripe_lib.Customer.retrieve(customer_id)
                email = (customer.get("metadata") or {}).get("app_email", "") or customer.get("email", "")
            except Exception:
                pass
        if not email:
            email = session.get("user_email", "")
        if not email:
            return jsonify({"error": "Could not identify user from Stripe session"}), 400

        email = email.lower().strip()

        # Ensure subscription row exists
        existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
        if not existing:
            db.execute(
                f"INSERT INTO subscriptions (user_email, stripe_customer_id, search_credits, created_at, updated_at) VALUES ({p}, {p}, 0, {p}, {p})",
                (email, customer_id, now_iso, now_iso),
            )
        elif customer_id:
            db.execute(
                f"UPDATE subscriptions SET stripe_customer_id = {p}, updated_at = {p} WHERE user_email = {p}",
                (customer_id, now_iso, email),
            )

        if mode == "payment":
            # Idempotency check: only add credits if this checkout session hasn't been processed before
            already_rows = db.query(
                f"SELECT last_checkout_session_id FROM subscriptions WHERE user_email = {p}",
                (email,),
            )
            already = (already_rows[0].get("last_checkout_session_id") or "") if already_rows else ""
            if already != checkout_session_id:
                db.execute(
                    f"UPDATE subscriptions SET search_credits = search_credits + 3, last_checkout_session_id = {p}, updated_at = {p} WHERE user_email = {p}",
                    (checkout_session_id, now_iso, email),
                )
        elif mode == "subscription":
            sub_id = cs.get("subscription", "")
            if sub_id:
                s = stripe_lib.Subscription.retrieve(sub_id)
                plan_type = _get_plan_type_from_stripe_sub(s)
                ts = _get_period_end_ts(s)
                period_end = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
                db.execute(
                    f"UPDATE subscriptions SET stripe_subscription_id = {p}, plan_type = {p}, sub_status = 'active', current_period_end = {p}, updated_at = {p} WHERE user_email = {p}",
                    (sub_id, plan_type, period_end, now_iso, email),
                )

        return jsonify({"ok": True, "mode": mode, "email": email})
    except Exception as e:
        app.logger.error(f"sync_subscription error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.get("/api/subscription/status")
def subscription_status() -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    access = get_user_access(email)
    return jsonify(access)


@app.post("/api/subscription/checkout")
def create_checkout_session() -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    stripe_lib = _get_stripe_lib()
    if not stripe_lib:
        return jsonify({"error": "Stripe is not configured on this server"}), 503

    data = request.get_json(silent=True) or {}
    price_id = str(data.get("price_id", "")).strip()
    mode = str(data.get("mode", "subscription")).strip()

    if not price_id:
        return jsonify({"error": "price_id is required"}), 400
    if mode not in ("subscription", "payment"):
        return jsonify({"error": "Invalid mode; must be 'subscription' or 'payment'"}), 400
    allowed_prices = {STRIPE_PRICE_MONTHLY, STRIPE_PRICE_ANNUAL, STRIPE_PRICE_SEARCH_PACK}
    if price_id not in allowed_prices:
        return jsonify({"error": "Invalid price selection"}), 400

    stripe_lib.api_key = STRIPE_SECRET_KEY
    p = db.placeholder

    try:
        # Get or create Stripe customer
        sub_rows = db.query(f"SELECT stripe_customer_id FROM subscriptions WHERE user_email = {p}", (email,))
        customer_id = (sub_rows[0].get("stripe_customer_id") or "") if sub_rows else ""

        # Verify the stored customer still exists in Stripe; create a new one if not
        if customer_id:
            try:
                stripe_lib.Customer.retrieve(customer_id)
            except stripe_lib.error.InvalidRequestError:
                customer_id = ""  # stale — will create fresh below

        if not customer_id:
            customer = stripe_lib.Customer.create(
                email=email,
                metadata={"app_email": email},
            )
            customer_id = customer.id
            now_iso = _now_utc().isoformat()
            existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (email,))
            if existing:
                db.execute(
                    f"UPDATE subscriptions SET stripe_customer_id = {p}, updated_at = {p} WHERE user_email = {p}",
                    (customer_id, now_iso, email),
                )
            else:
                db.execute(
                    f"INSERT INTO subscriptions (user_email, stripe_customer_id, search_credits, created_at, updated_at) VALUES ({p}, {p}, 0, {p}, {p})",
                    (email, customer_id, now_iso, now_iso),
                )

        checkout = stripe_lib.checkout.Session.create(
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            mode=mode,
            success_url=request.host_url.rstrip("/") + "/subscription/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url.rstrip("/") + "/pricing",
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        app.logger.error(f"create_checkout_session error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.post("/api/subscription/upgrade")
def upgrade_subscription() -> Any:
    """Upgrade an active monthly subscription to annual in-place via Stripe."""
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    stripe_lib = _get_stripe_lib()
    if not stripe_lib:
        return jsonify({"error": "Stripe is not configured on this server"}), 503
    if not STRIPE_PRICE_ANNUAL:
        return jsonify({"error": "Annual plan not configured"}), 503

    p = db.placeholder
    sub_rows = db.query(f"SELECT * FROM subscriptions WHERE user_email = {p}", (email,))
    if not sub_rows or not sub_rows[0].get("stripe_subscription_id"):
        return jsonify({"error": "No active subscription found"}), 400
    sub = sub_rows[0]
    if sub.get("plan_type") != "monthly":
        return jsonify({"error": "Only monthly plans can be upgraded this way"}), 400

    stripe_lib.api_key = STRIPE_SECRET_KEY
    try:
        stripe_sub = stripe_lib.Subscription.retrieve(sub["stripe_subscription_id"])
        item_id = stripe_sub["items"]["data"][0]["id"]
        stripe_lib.Subscription.modify(
            sub["stripe_subscription_id"],
            items=[{"id": item_id, "price": STRIPE_PRICE_ANNUAL}],
            proration_behavior="always_invoice",
        )
        now_iso = _now_utc().isoformat()
        db.execute(
            f"UPDATE subscriptions SET plan_type = {p}, updated_at = {p} WHERE user_email = {p}",
            ("annual", now_iso, email),
        )
        return jsonify({"success": True})
    except Exception as e:
        app.logger.error(f"upgrade_subscription error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.post("/api/subscription/portal")
def customer_portal() -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    stripe_lib = _get_stripe_lib()
    if not stripe_lib:
        return jsonify({"error": "Stripe is not configured on this server"}), 503

    stripe_lib.api_key = STRIPE_SECRET_KEY
    p = db.placeholder

    sub_rows = db.query(f"SELECT stripe_customer_id FROM subscriptions WHERE user_email = {p}", (email,))
    customer_id = (sub_rows[0].get("stripe_customer_id") or "") if sub_rows else ""
    if not customer_id:
        return jsonify({"error": "No billing account found. Please subscribe first."}), 404

    # Verify customer still exists in Stripe
    try:
        stripe_lib.Customer.retrieve(customer_id)
    except stripe_lib.error.InvalidRequestError:
        return jsonify({"error": "Billing account not found. Please contact support."}), 404

    try:
        portal = stripe_lib.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.host_url.rstrip("/") + "/account",
        )
        return jsonify({"url": portal.url})
    except Exception as e:
        app.logger.error(f"customer_portal error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.post("/api/subscription/cancel")
def cancel_subscription() -> Any:
    email = session.get("user_email")
    if not email:
        return jsonify({"error": "Login required"}), 401
    stripe_lib = _get_stripe_lib()
    if not stripe_lib:
        return jsonify({"error": "Stripe is not configured on this server"}), 503

    stripe_lib.api_key = STRIPE_SECRET_KEY
    p = db.placeholder

    sub_rows = db.query(f"SELECT stripe_subscription_id FROM subscriptions WHERE user_email = {p}", (email,))
    sub_id = (sub_rows[0].get("stripe_subscription_id") or "") if sub_rows else ""
    if not sub_id:
        return jsonify({"error": "No active subscription found"}), 404

    try:
        stripe_lib.Subscription.modify(sub_id, cancel_at_period_end=True)
        return jsonify({"ok": True, "message": "Subscription will cancel at the end of the current billing period."})
    except Exception as e:
        app.logger.error(f"cancel_subscription error: {e}")
        return jsonify({"error": "An internal error occurred. Please try again."}), 500


@app.post("/webhooks/stripe")
@csrf.exempt
def stripe_webhook() -> Any:
    stripe_lib = _get_stripe_lib()
    if not stripe_lib:
        return jsonify({"error": "Stripe not configured"}), 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        stripe_lib.api_key = STRIPE_SECRET_KEY
        event = stripe_lib.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe_lib.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except Exception as e:
        app.logger.error(f"stripe_webhook parse error: {e}")
        return jsonify({"error": "Invalid request."}), 400

    event_type = event["type"]
    obj = event["data"]["object"]
    now_iso = _now_utc().isoformat()
    p = db.placeholder

    if event_type == "checkout.session.completed":
        mode = obj.get("mode")
        customer_id = obj.get("customer", "")
        # Retrieve user email from customer metadata
        user_email = ""
        try:
            customer = stripe_lib.Customer.retrieve(customer_id)
            user_email = customer.get("metadata", {}).get("app_email", "") or customer.get("email", "")
        except Exception:
            user_email = obj.get("customer_details", {}).get("email", "")

        if not user_email:
            return jsonify({"ok": True})

        user_email = user_email.lower().strip()

        # Ensure row exists
        existing = db.query(f"SELECT user_email FROM subscriptions WHERE user_email = {p}", (user_email,))
        if not existing:
            db.execute(
                f"INSERT INTO subscriptions (user_email, stripe_customer_id, search_credits, created_at, updated_at) VALUES ({p}, {p}, 0, {p}, {p})",
                (user_email, customer_id, now_iso, now_iso),
            )
        else:
            db.execute(
                f"UPDATE subscriptions SET stripe_customer_id = {p}, updated_at = {p} WHERE user_email = {p}",
                (customer_id, now_iso, user_email),
            )

        if mode == "payment":
            # Search pack: add 3 credits — idempotency check prevents double-grant on retry
            checkout_session_id = obj.get("id", "")
            already_rows = db.query(
                f"SELECT last_checkout_session_id FROM subscriptions WHERE user_email = {p}",
                (user_email,),
            )
            already = (already_rows[0].get("last_checkout_session_id") or "") if already_rows else ""
            if already != checkout_session_id:
                db.execute(
                    f"UPDATE subscriptions SET search_credits = search_credits + 3, last_checkout_session_id = {p}, updated_at = {p} WHERE user_email = {p}",
                    (checkout_session_id, now_iso, user_email),
                )
        elif mode == "subscription":
            sub_id = obj.get("subscription", "")
            if sub_id:
                try:
                    sub = stripe_lib.Subscription.retrieve(sub_id)
                    plan_type = _get_plan_type_from_stripe_sub(sub)
                    ts = _get_period_end_ts(sub)
                    period_end = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
                    db.execute(
                        f"UPDATE subscriptions SET stripe_subscription_id = {p}, plan_type = {p}, sub_status = 'active', current_period_end = {p}, updated_at = {p} WHERE user_email = {p}",
                        (sub_id, plan_type, period_end, now_iso, user_email),
                    )
                except Exception:
                    pass

    elif event_type == "invoice.payment_succeeded":
        sub_id = obj.get("subscription", "")
        customer_id = obj.get("customer", "")
        if sub_id:
            try:
                sub = stripe_lib.Subscription.retrieve(sub_id)
                plan_type = _get_plan_type_from_stripe_sub(sub)
                ts = _get_period_end_ts(sub)
                period_end = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
                # Primary: update by subscription_id (covers renewals)
                db.execute(
                    f"UPDATE subscriptions SET sub_status = 'active', current_period_end = {p}, plan_type = {p}, updated_at = {p} WHERE stripe_subscription_id = {p}",
                    (period_end, plan_type, now_iso, sub_id),
                )
                # Fallback: if the subscription_id wasn't saved (initial sync failed),
                # update by customer_id so the user still gets access.
                if customer_id:
                    db.execute(
                        f"UPDATE subscriptions SET sub_status = 'active', stripe_subscription_id = {p}, current_period_end = {p}, plan_type = {p}, updated_at = {p} WHERE stripe_customer_id = {p} AND (stripe_subscription_id = '' OR stripe_subscription_id IS NULL)",
                        (sub_id, period_end, plan_type, now_iso, customer_id),
                    )
            except Exception:
                pass

    elif event_type == "invoice.payment_failed":
        sub_id = obj.get("subscription", "")
        if sub_id:
            db.execute(
                f"UPDATE subscriptions SET sub_status = 'past_due', updated_at = {p} WHERE stripe_subscription_id = {p}",
                (now_iso, sub_id),
            )

    elif event_type == "customer.subscription.deleted":
        sub_id = obj.get("id", "")
        if sub_id:
            db.execute(
                f"UPDATE subscriptions SET sub_status = 'canceled', stripe_subscription_id = '', plan_type = '', current_period_end = '', updated_at = {p} WHERE stripe_subscription_id = {p}",
                (now_iso, sub_id),
            )

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

try:
    if DB_ENGINE == "sqlite" and DATABASE_PATH not in (":memory:",):
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    if AUTO_INIT_DB:
        with app.app_context():
            db.init_schema()
            db.ensure_columns()
        _db_initialized = True
except Exception as exc:
    print(f"[startup] database initialization failed: {exc}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=os.getenv("FLASK_DEBUG", "0") == "1")
