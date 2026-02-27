const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const airportHint = document.querySelector('#airport_hint');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const suggestionsSection = document.querySelector('#suggestions-section');
const suggestionList = document.querySelector('#suggestion-list');
const departureDateInput = document.querySelector('#departure_date');
const airlineDropdown = document.querySelector('#airline-dropdown');
const airlineNameInput = document.querySelector('#airline_name');

// --- Set min date to today ---
if (departureDateInput) {
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  departureDateInput.min = `${yyyy}-${mm}-${dd}`;

  // Also set a reasonable max (1 year from now)
  const maxDate = new Date(today);
  maxDate.setFullYear(maxDate.getFullYear() + 1);
  const maxYyyy = maxDate.getFullYear();
  const maxMm = String(maxDate.getMonth() + 1).padStart(2, '0');
  const maxDd = String(maxDate.getDate()).padStart(2, '0');
  departureDateInput.max = `${maxYyyy}-${maxMm}-${maxDd}`;
}

// --- Formatters ---

const formatPhone = (raw) => {
  const digits = raw.replace(/\D/g, '').slice(0, 11);
  const withCountry = digits.startsWith('1') ? digits : `1${digits}`;
  const limited = withCountry.slice(0, 11);
  const a = limited.slice(1, 4);
  const b = limited.slice(4, 7);
  const c = limited.slice(7, 11);
  let out = '+1';
  if (a) out += ` (${a}`;
  if (a.length === 3) out += ')';
  if (b) out += ` ${b}`;
  if (c) out += ` ${c}`;
  return out;
};

// --- Input listeners ---

phoneInput?.addEventListener('input', () => {
  phoneInput.value = formatPhone(phoneInput.value);
});

// --- Date validation ---
departureDateInput?.addEventListener('change', () => {
  const val = departureDateInput.value;
  if (!val) return;

  const selected = new Date(val + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  if (selected < today) {
    departureDateInput.value = '';
    departureDateInput.setCustomValidity('Please select a date today or in the future.');
    departureDateInput.reportValidity();
    return;
  }
  departureDateInput.setCustomValidity('');

  // Trigger flight suggestions if we have enough of a flight code
  if (flightCodeInput?.value?.length >= 3) {
    clearTimeout(suggestTimeout);
    suggestTimeout = setTimeout(fetchFlightSuggestions, 300);
  }
});

// --- Airline autocomplete ---

let airlineActiveIdx = -1;
let airlineResults = [];

const closeAirlineDropdown = () => {
  if (airlineDropdown) {
    airlineDropdown.classList.remove('open');
    airlineDropdown.innerHTML = '';
  }
  airlineActiveIdx = -1;
  airlineResults = [];
};

const showAirlineDropdown = (matches) => {
  if (!airlineDropdown || !matches.length) {
    closeAirlineDropdown();
    return;
  }
  airlineResults = matches;
  airlineActiveIdx = -1;
  airlineDropdown.innerHTML = '';

  matches.forEach((airline, i) => {
    const opt = document.createElement('div');
    opt.className = 'airline-option';
    opt.innerHTML = `<span class="code">${airline.code}</span><span class="name">${airline.name}</span>`;
    opt.addEventListener('mousedown', (e) => {
      e.preventDefault();
      flightCodeInput.value = airline.code;
      if (airlineNameInput) airlineNameInput.value = airline.name;
      closeAirlineDropdown();
      flightCodeInput.focus();
    });
    airlineDropdown.appendChild(opt);
  });

  airlineDropdown.classList.add('open');
};

const fetchAirlineSuggestions = async (prefix) => {
  try {
    const res = await fetch(`/api/airlines/suggest?q=${encodeURIComponent(prefix)}`);
    const data = await res.json();
    return data.results || [];
  } catch {
    return [];
  }
};

// --- Flight suggestions (SerpApi, debounced) ---

let suggestTimeout = null;
let lastSuggestQuery = '';

const fetchFlightSuggestions = async () => {
  const q = flightCodeInput.value;
  const dateVal = departureDateInput?.value || '';

  // Need at least airline prefix (2 chars) to search
  if (q.length < 2) {
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
    return;
  }

  // Build a cache key to avoid duplicate requests
  const cacheKey = `${q}|${dateVal}`;
  if (cacheKey === lastSuggestQuery) return;
  lastSuggestQuery = cacheKey;

  // Show loading state
  suggestionsSection.style.display = 'block';
  suggestionList.innerHTML = '<div class="suggestion-loading"><span class="spinner"></span> Searching for flights...</div>';

  // Convert date to MM-DD-YYYY for the API if it's in YYYY-MM-DD format
  let dateParam = '';
  if (dateVal) {
    const parts = dateVal.split('-');
    if (parts.length === 3 && parts[0].length === 4) {
      dateParam = `&departure_date=${encodeURIComponent(`${parts[1]}-${parts[2]}-${parts[0]}`)}`;
    } else {
      dateParam = `&departure_date=${encodeURIComponent(dateVal)}`;
    }
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);

    const res = await fetch(
      `/api/flights/suggest?query=${encodeURIComponent(q)}${dateParam}`,
      { signal: controller.signal }
    );
    clearTimeout(timeout);
    const data = await res.json();

    suggestionList.innerHTML = '';

    if (!data.results?.length) {
      if (q.length >= 3) {
        suggestionList.innerHTML = '<div class="suggestion-empty">No flights found. Try a different date or flight code.</div>';
      } else {
        suggestionsSection.style.display = 'none';
      }
      return;
    }

    suggestionsSection.style.display = 'block';

    data.results.forEach((flight) => {
      const item = document.createElement('div');
      item.className = 'suggestion-item';
      item.innerHTML = `
        <div>
          <span class="suggestion-flight-code">${flight.flight_code}</span>
          <span class="suggestion-route">${flight.departure} &rarr; ${flight.destination}</span>
        </div>
        <div class="suggestion-time">
          ${flight.time_utc} UTC<br/>
          <small>${flight.date_utc || ''}</small>
        </div>
      `;

      item.addEventListener('click', async () => {
        flightCodeInput.value = flight.flight_code;

        // Auto-fill departure airport
        if (flight.departure && airportCodeInput) {
          airportCodeInput.value = flight.departure;
          airportCodeInput.classList.add('auto-filled');
          if (airportHint) airportHint.textContent = flight.departure_name || `Airport: ${flight.departure}`;
        }

        // Auto-fill date if provided
        if (flight.date_utc && departureDateInput) {
          departureDateInput.value = flight.date_utc; // Already YYYY-MM-DD format
        }

        // Auto-fill airline name
        if (airlineNameInput) {
          if (flight.airline) {
            airlineNameInput.value = flight.airline;
          } else {
            const prefix = flight.flight_code.match(/^[A-Z]{2,3}/)?.[0] || '';
            if (prefix) {
              const matches = await fetchAirlineSuggestions(prefix);
              const exact = matches.find(m => m.code === prefix);
              if (exact) airlineNameInput.value = exact.name;
            }
          }
        }

        suggestionList.querySelectorAll('.suggestion-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
      });

      suggestionList.appendChild(item);
    });
  } catch (err) {
    if (err.name !== 'AbortError') {
      suggestionList.innerHTML = '<div class="suggestion-empty">Failed to load suggestions. Please try again.</div>';
    }
  }
};

// --- Combined flight code input handler ---

flightCodeInput?.addEventListener('input', async () => {
  const raw = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
  flightCodeInput.value = raw;

  // Reset airport when flight code changes (user is typing a new code)
  if (airportCodeInput && airportCodeInput.classList.contains('auto-filled')) {
    airportCodeInput.value = '';
    airportCodeInput.classList.remove('auto-filled');
    if (airportHint) airportHint.textContent = 'Select a flight suggestion to auto-fill';
  }

  const letterPrefix = raw.match(/^[A-Z0-9]{0,3}/)?.[0] || '';
  const hasDigits = /\d/.test(raw);

  // Show airline dropdown only when typing the airline prefix (letters only, 1-3 chars)
  if (letterPrefix.length >= 1 && letterPrefix.length <= 3 && !hasDigits) {
    const matches = await fetchAirlineSuggestions(letterPrefix);
    showAirlineDropdown(matches);
    if (airlineNameInput) {
      const exact = matches.find(m => m.code === letterPrefix);
      airlineNameInput.value = exact ? exact.name : (matches.length === 1 ? matches[0].name : '');
    }
  } else {
    closeAirlineDropdown();
    if (airlineNameInput && letterPrefix.length >= 2) {
      const pureLetters = raw.match(/^[A-Z]{2,3}/)?.[0] || '';
      if (pureLetters && !airlineNameInput.value) {
        const matches = await fetchAirlineSuggestions(pureLetters);
        const exact = matches.find(m => m.code === pureLetters);
        if (exact) airlineNameInput.value = exact.name;
      }
    }
  }

  // Fetch flight suggestions once we have at least airline code (2 chars) + some digits
  clearTimeout(suggestTimeout);
  if (raw.length >= 3 && hasDigits) {
    suggestTimeout = setTimeout(fetchFlightSuggestions, 500);
  } else if (raw.length >= 2 && !hasDigits && departureDateInput?.value) {
    // Also search with just the airline prefix if a date is selected
    suggestTimeout = setTimeout(fetchFlightSuggestions, 600);
  } else {
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
    lastSuggestQuery = '';
  }
});

// Keyboard navigation for airline dropdown
flightCodeInput?.addEventListener('keydown', (e) => {
  if (!airlineDropdown?.classList.contains('open') || !airlineResults.length) return;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    airlineActiveIdx = Math.min(airlineActiveIdx + 1, airlineResults.length - 1);
    updateActiveAirline();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    airlineActiveIdx = Math.max(airlineActiveIdx - 1, 0);
    updateActiveAirline();
  } else if (e.key === 'Enter' && airlineActiveIdx >= 0) {
    e.preventDefault();
    flightCodeInput.value = airlineResults[airlineActiveIdx].code;
    if (airlineNameInput) airlineNameInput.value = airlineResults[airlineActiveIdx].name;
    closeAirlineDropdown();
  } else if (e.key === 'Escape') {
    closeAirlineDropdown();
  }
});

const updateActiveAirline = () => {
  const opts = airlineDropdown?.querySelectorAll('.airline-option') || [];
  opts.forEach((opt, i) => opt.classList.toggle('active', i === airlineActiveIdx));
};

// Close dropdown when clicking outside
flightCodeInput?.addEventListener('blur', () => {
  setTimeout(closeAirlineDropdown, 150);
});

// --- Form submission ---

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  closeAirlineDropdown();

  // Validate date is selected and valid
  if (!departureDateInput?.value) {
    msg.textContent = 'Please select a departure date from the calendar.';
    msg.className = 'error';
    departureDateInput?.focus();
    return;
  }

  const selectedDate = new Date(departureDateInput.value + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (selectedDate < today) {
    msg.textContent = 'Departure date cannot be in the past.';
    msg.className = 'error';
    departureDateInput?.focus();
    return;
  }

  // Validate airport is filled
  if (!airportCodeInput?.value) {
    msg.textContent = 'Departure airport is required. Please select a flight from the suggestions above to auto-fill it, or type your flight code and pick a suggestion.';
    msg.className = 'error';
    flightCodeInput?.focus();
    return;
  }

  msg.textContent = 'Saving your flight details...';
  msg.className = '';

  const submitBtn = form.querySelector('button[type="submit"]');
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span class="spinner"></span> Saving...';

  const payload = Object.fromEntries(new FormData(form).entries());
  payload.flight_code = String(payload.flight_code || '').toUpperCase().replace(/\s+/g, '');

  // Convert YYYY-MM-DD to MM-DD-YYYY for the backend
  if (payload.departure_date && payload.departure_date.includes('-')) {
    const parts = payload.departure_date.split('-');
    if (parts.length === 3 && parts[0].length === 4) {
      payload.departure_date = `${parts[1]}-${parts[2]}-${parts[0]}`;
    }
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    const res = await fetch('/api/carpools', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    clearTimeout(timeout);

    const data = await res.json();
    if (!res.ok) {
      msg.textContent = data.error || 'Could not submit. Please check your inputs.';
      msg.className = 'error';
      return;
    }

    let text = 'Your flight has been saved to the carpool database!';
    if (data.warning) {
      text += ` Note: ${data.warning}`;
    }
    msg.textContent = text;
    msg.className = '';
    form.reset();
    if (airlineNameInput) airlineNameInput.value = '';
    if (airportCodeInput) {
      airportCodeInput.value = '';
      airportCodeInput.classList.remove('auto-filled');
    }
    if (airportHint) airportHint.textContent = 'Select a flight suggestion to auto-fill';
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
    lastSuggestQuery = '';
  } catch (err) {
    if (err.name === 'AbortError') {
      msg.textContent = 'Request timed out. Please try again.';
    } else {
      msg.textContent = 'Network error. Please check your connection and try again.';
    }
    msg.className = 'error';
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
  }
});
