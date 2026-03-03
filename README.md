# Campus2Air - University Carpool Matchmaking

Campus2Air is a web application built for university students to coordinate shared rides to the airport. Students enter their flight details, and the system matches them with others on the same flight or heading to the same airport on the same day, so they can split the cost of a ride from campus.

## What It Does

- **Add Flight Details** - Students submit their flight code, departure airport, date, planned campus departure time, and contact info.
- **Find a Carpool** - Search for other students flying on the same flight, from the same airport, or on the same date. Results are ranked by match score.
- **Live Flight Verification** - Flight codes are verified in real-time using the SerpApi Google Flights API. Airline names auto-detect from the flight code prefix.
- **Automatic Cleanup** - All records are automatically deleted at the end of the departure day (11:59 PM), so no stale data lingers.
- **Admin Panel** - A password-protected admin dashboard for managing entries, with session-based authentication and 30-minute session expiry.

## Built For

This project was developed as part of a university coursework assignment to solve a real problem: helping students find others to share rides to the airport and reduce transportation costs.

## Technology Stack

- **Backend**: Python / Flask
- **Database**: MySQL (TiDB Cloud) with SQLite fallback
- **Flight Data**: SerpApi Google Flights API
- **Frontend**: HTML, CSS (custom dark theme), vanilla JavaScript
- **Deployment**: Vercel (serverless Python runtime)
- **Authentication**: Werkzeug password hashing with session-based admin auth


## Security Configuration

- `FLASK_SECRET_KEY` strongly recommended in all environments; if missing, the app uses an ephemeral in-memory secret so deployments still boot.
- `ADMIN_PASSWORD_HASH` sets admin password hash (recommended).
- `ADMIN_PASSWORD` optional plaintext fallback for backward compatibility when no hash is provided (default fallback remains legacy value).
- `ADMIN_LOGIN_DISABLED` optional kill switch (`true/1/yes`) to disable admin login entirely.
- `FIREBASE_PROJECT_ID` expected Firebase project for verified ID tokens.
- `FIREBASE_STRICT_VERIFICATION` optional hard-fail switch (`true/1/yes`) to require server-side ID token verification (recommended for production).
- `FIREBASE_LEGACY_FALLBACK` optional emergency compatibility mode (`true/1/yes`) to allow legacy client-provided identity when verifier is unavailable (not recommended).
- `HEALTHCHECK_TOKEN` optional token gate for `/health` endpoint (`/health?token=...`).
- `REQUIRE_SECRET_KEY` optional hard-fail switch (`true/1/yes`) to force startup failure when `FLASK_SECRET_KEY` is missing.
- `SESSION_COOKIE_SECURE` optional override (`true/false`) for the secure-cookie flag (defaults to `true` outside `FLASK_ENV=development`).

Generate an admin hash locally:

```bash
python - <<'PY'
from werkzeug.security import generate_password_hash
print(generate_password_hash('replace-with-strong-password'))
PY
```
