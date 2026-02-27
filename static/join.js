const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const destAirportInput = document.querySelector('#destination_airport');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const suggestionsSection = document.querySelector('#suggestions-section');
const suggestionList = document.querySelector('#suggestion-list');
const departureDateInput = document.querySelector('#departure_date');
const airlineDropdown = document.querySelector('#airline-dropdown');
const airlineNameInput = document.querySelector('#airline_name');

// --- Formatters ---

const formatDate = (raw) => {
  const digits = raw.replace(/\D/g, '').slice(0, 8);
  const mm = digits.slice(0, 2);
  const dd = digits.slice(2, 4);
  const yyyy = digits.slice(4, 8);
  let out = mm;
  if (dd) out += `-${dd}`;
  if (yyyy) out += `-${yyyy}`;
  return out;
};

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

airportCodeInput?.addEventListener('input', () => {
  airportCodeInput.value = airportCodeInput.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

destAirportInput?.addEventListener('input', () => {
  destAirportInput.value = destAirportInput.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

phoneInput?.addEventListener('input', () => {
  phoneInput.value = formatPhone(phoneInput.value);
});

departureDateInput?.addEventListener('input', () => {
  departureDateInput.value = formatDate(departureDateInput.value);
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
      e.preventDefault(); // prevent blur from closing dropdown
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

const fetchFlightSuggestions = async () => {
  const q = flightCodeInput.value;
  if (q.length < 4) {
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
    return;
  }

  const dateParam = departureDateInput?.value
    ? `&departure_date=${encodeURIComponent(departureDateInput.value)}`
    : '';

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);

    const res = await fetch(
      `/api/flights/suggest?query=${encodeURIComponent(q)}${dateParam}`,
      { signal: controller.signal }
    );
    clearTimeout(timeout);
    const data = await res.json();

    suggestionList.innerHTML = '';

    if (!data.results?.length) {
      suggestionsSection.style.display = 'none';
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

        if (flight.departure && airportCodeInput) {
          airportCodeInput.value = flight.departure;
        }
        if (flight.destination && destAirportInput) {
          destAirportInput.value = flight.destination;
        }
        if (flight.date_utc && departureDateInput) {
          const parts = flight.date_utc.split('-');
          if (parts.length === 3) {
            departureDateInput.value = `${parts[1]}-${parts[2]}-${parts[0]}`;
          }
        }

        // Auto-fill airline name from the flight's airline field or code prefix
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
  } catch {
    suggestionsSection.style.display = 'none';
  }
};

// --- Combined flight code input handler ---

flightCodeInput?.addEventListener('input', async () => {
  const raw = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
  flightCodeInput.value = raw;

  // Extract the letter prefix (airline code part)
  const letterPrefix = raw.match(/^[A-Z0-9]{0,3}/)?.[0] || '';
  const hasDigits = /\d/.test(raw);

  // Show airline dropdown only when typing the airline prefix (letters only, 1-3 chars)
  if (letterPrefix.length >= 1 && letterPrefix.length <= 3 && !hasDigits) {
    const matches = await fetchAirlineSuggestions(letterPrefix);
    showAirlineDropdown(matches);
    // Auto-fill airline name if there's an exact code match
    if (airlineNameInput) {
      const exact = matches.find(m => m.code === letterPrefix);
      airlineNameInput.value = exact ? exact.name : (matches.length === 1 ? matches[0].name : '');
    }
  } else {
    closeAirlineDropdown();
    // When digits are present, try to resolve airline from the prefix
    if (airlineNameInput && letterPrefix.length >= 2) {
      const pureLetters = raw.match(/^[A-Z]{2,3}/)?.[0] || '';
      if (pureLetters && !airlineNameInput.value) {
        const matches = await fetchAirlineSuggestions(pureLetters);
        const exact = matches.find(m => m.code === pureLetters);
        if (exact) airlineNameInput.value = exact.name;
      }
    }
  }

  // Once user has a full flight code (4+ chars), fetch SerpApi suggestions
  clearTimeout(suggestTimeout);
  if (raw.length >= 4) {
    suggestTimeout = setTimeout(fetchFlightSuggestions, 600);
  } else {
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
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
  // Small delay to allow mousedown on dropdown items to fire first
  setTimeout(closeAirlineDropdown, 150);
});

// Also trigger flight suggestions when date changes
departureDateInput?.addEventListener('change', () => {
  if (flightCodeInput?.value?.length >= 4) {
    clearTimeout(suggestTimeout);
    suggestTimeout = setTimeout(fetchFlightSuggestions, 300);
  }
});

// --- Form submission ---

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  closeAirlineDropdown();
  msg.textContent = 'Saving your flight details...';
  msg.className = '';

  const submitBtn = form.querySelector('button[type="submit"]');
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span class="spinner"></span> Saving...';

  const payload = Object.fromEntries(new FormData(form).entries());
  payload.flight_code = String(payload.flight_code || '').toUpperCase().replace(/\s+/g, '');

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
    suggestionsSection.style.display = 'none';
    suggestionList.innerHTML = '';
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
