# Campus2Air - University Carpool Matchmaking

Campus2Air is a web application built for university students to coordinate shared rides to the airport. Students enter their flight details, and the system matches them with others on the same flight or heading to the same airport on the same day, so they can split the cost of a ride from campus.

## What It Does

- **Add Flight Details** - Students submit their flight code, departure airport, date, planned campus departure time, and contact info.
- **Find a Carpool** - Search for other students flying on the same flight, from the same airport, or on the same date. Results are ranked by match score.
- **Airline Autocomplete** - Airline names auto-detect from the flight code prefix using a built-in IATA code lookup.
- **In-App Notifications** - Users receive in-app alerts when someone joins or leaves their carpool, when ownership transfers, and when a carpool is disbanded.
- **Automatic Cleanup** - All records are automatically deleted at the end of the departure day (11:59 PM UTC), so no stale data lingers.
- **Admin Panel** - A password-protected admin dashboard for managing entries, with session-based authentication and 30-minute session expiry.

## Built For

This project was developed as part of a university coursework assignment to solve a real problem: helping students find others to share rides to the airport and reduce transportation costs.

## Technology Stack

- **Backend**: Python / Flask
- **Database**: MySQL (TiDB Cloud) with SQLite fallback
- **Frontend**: HTML, CSS (custom dark theme), vanilla JavaScript
- **Deployment**: Vercel (serverless Python runtime)
- **Authentication**: Firebase (Google Sign-In for users), Werkzeug password hashing with session-based admin auth
