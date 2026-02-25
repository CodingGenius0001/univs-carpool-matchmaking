const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const destAirportInput = document.querySelector('#destination_airport');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const suggestionsSection = document.querySelector('#suggestions-section');
const suggestionList = document.querySelector('#suggestion-list');
const departureDateInput = document.querySelector('#departure_date');

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

flightCodeInput?.addEventListener('input', () => {
  flightCodeInput.value = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
});

// --- Flight suggestions (debounced) ---

let suggestTimeout = null;

const fetchSuggestions = async () => {
  const q = flightCodeInput.value;
  if (q.length < 3) {
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

      item.addEventListener('click', () => {
        // Auto-fill fields from suggestion
        flightCodeInput.value = flight.flight_code;

        if (flight.departure && airportCodeInput) {
          airportCodeInput.value = flight.departure;
        }
        if (flight.destination && destAirportInput) {
          destAirportInput.value = flight.destination;
        }
        if (flight.date_utc && departureDateInput) {
          // Convert YYYY-MM-DD to MM-DD-YYYY
          const parts = flight.date_utc.split('-');
          if (parts.length === 3) {
            departureDateInput.value = `${parts[1]}-${parts[2]}-${parts[0]}`;
          }
        }

        // Highlight selected
        suggestionList.querySelectorAll('.suggestion-item').forEach(el => el.classList.remove('selected'));
        item.classList.add('selected');
      });

      suggestionList.appendChild(item);
    });
  } catch (err) {
    // Silently fail - suggestions are optional
    suggestionsSection.style.display = 'none';
  }
};

flightCodeInput?.addEventListener('input', () => {
  clearTimeout(suggestTimeout);
  suggestTimeout = setTimeout(fetchSuggestions, 500);
});

departureDateInput?.addEventListener('change', () => {
  if (flightCodeInput?.value?.length >= 3) {
    clearTimeout(suggestTimeout);
    suggestTimeout = setTimeout(fetchSuggestions, 300);
  }
});

// --- Form submission ---

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
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
