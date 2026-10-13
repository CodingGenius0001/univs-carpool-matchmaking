// Shared helpers for showing a carpool's trip at a glance:
// "UCR → ONT · Fri, Oct 23" plus a "Tomorrow" / "In 5 days" countdown.
(function () {
  const CAMPUS = 'UCR';
  const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // Stored dates are MM-DD-YYYY (the flight / landing date).
  function parseDate(stored) {
    const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(stored || ''));
    return m ? new Date(Number(m[3]), Number(m[1]) - 1, Number(m[2])) : null;
  }

  function friendlyDate(stored) {
    const d = parseDate(stored);
    return d ? `${WEEKDAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}` : String(stored || '').replace(/-/g, '/');
  }

  function daysUntil(stored) {
    const d = parseDate(stored);
    if (!d) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((d - today) / 86400000);
  }

  function countdown(stored) {
    const n = daysUntil(stored);
    if (n === null) return '';
    if (n === 0) return 'Today';
    if (n === 1) return 'Tomorrow';
    if (n === -1) return 'Yesterday';
    if (n < 0) return 'Past';
    return `In ${n} days`;
  }

  function isFrom(c) {
    return c?.direction === 'from_airport';
  }

  // 'UCR → ONT' / 'ONT → UCR'. Plain text; callers escape before inserting into HTML.
  function route(c) {
    const airport = c?.airport_code || 'Airport';
    return isFrom(c) ? `${airport} → ${CAMPUS}` : `${CAMPUS} → ${airport}`;
  }

  function headline(c) {
    return `${route(c)} · ${friendlyDate(c?.requested_flight_date)}`;
  }

  function directionLabel(c) {
    return isFrom(c) ? `Airport → ${CAMPUS}` : `${CAMPUS} → Airport`;
  }

  function tripClass(c) {
    return isFrom(c) ? 'trip-from' : 'trip-to';
  }

  // Soonest trip first; same day sorted by time.
  function compare(a, b) {
    const da = parseDate(a?.requested_flight_date);
    const dbb = parseDate(b?.requested_flight_date);
    const diff = (da ? da.getTime() : Infinity) - (dbb ? dbb.getTime() : Infinity);
    if (diff) return diff;
    return String(a?.planned_departure_time || '').localeCompare(String(b?.planned_departure_time || ''));
  }

  window.Trip = { CAMPUS, route, parseDate, friendlyDate, daysUntil, countdown, headline, directionLabel, tripClass, isFrom, compare };
})();
