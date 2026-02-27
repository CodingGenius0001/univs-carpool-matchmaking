const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const departureDateInput = document.querySelector('#departure_date');
const airlineDropdown = document.querySelector('#airline-dropdown');
const airlineNameInput = document.querySelector('#airline_name');
const firstNameInput = document.querySelector('#first_name');
const lastInitialInput = document.querySelector('#last_initial');

// --- Set min date to today ---
if (departureDateInput) {
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  departureDateInput.min = `${yyyy}-${mm}-${dd}`;

  const maxDate = new Date(today);
  maxDate.setFullYear(maxDate.getFullYear() + 1);
  const maxYyyy = maxDate.getFullYear();
  const maxMm = String(maxDate.getMonth() + 1).padStart(2, '0');
  const maxDd = String(maxDate.getDate()).padStart(2, '0');
  departureDateInput.max = `${maxYyyy}-${maxMm}-${maxDd}`;
}

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

// Strip anything that isn't a letter, space, hyphen, or apostrophe
const sanitizeName = (raw) => raw.replace(/[^A-Za-z \-']/g, '');

// --- Input listeners ---

// Name: only letters, spaces, hyphens, apostrophes
firstNameInput?.addEventListener('input', () => {
  firstNameInput.value = sanitizeName(firstNameInput.value);
});

// Last initial: only a single letter
lastInitialInput?.addEventListener('input', () => {
  lastInitialInput.value = lastInitialInput.value.replace(/[^A-Za-z]/g, '').slice(0, 1);
});

// Airport code: uppercase letters only
airportCodeInput?.addEventListener('input', () => {
  airportCodeInput.value = airportCodeInput.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

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

  matches.forEach((airline) => {
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

// --- Flight code input handler (airline autocomplete only) ---

flightCodeInput?.addEventListener('input', async () => {
  const raw = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
  flightCodeInput.value = raw;

  // Airline codes can contain digits (e.g. F9, 9K, G4)
  // Show airline dropdown while typing short codes (1-3 chars)
  if (raw.length >= 1 && raw.length <= 3) {
    const matches = await fetchAirlineSuggestions(raw);
    if (matches.length) {
      showAirlineDropdown(matches);
      if (airlineNameInput) {
        const exact = matches.find(m => m.code === raw);
        if (exact) {
          airlineNameInput.value = exact.name;
        } else if (matches.length === 1) {
          airlineNameInput.value = matches[0].name;
        }
      }
    } else {
      closeAirlineDropdown();
    }
  } else {
    closeAirlineDropdown();
    // Once user is typing flight number, try to resolve airline from prefix
    if (airlineNameInput && raw.length >= 3 && !airlineNameInput.value) {
      // Try 3-char prefix, then 2-char (covers F9xxx, 9Kxxx, AAxxxx, etc.)
      for (const len of [3, 2]) {
        if (raw.length < len) continue;
        const prefix = raw.slice(0, len);
        const matches = await fetchAirlineSuggestions(prefix);
        const exact = matches.find(m => m.code === prefix);
        if (exact) {
          airlineNameInput.value = exact.name;
          break;
        }
      }
    }
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

  // Validate name contains only letters
  const firstName = firstNameInput?.value || '';
  if (!firstName || /[^A-Za-z \-']/.test(firstName)) {
    msg.textContent = 'First name can only contain letters, spaces, hyphens, and apostrophes.';
    msg.className = 'error';
    firstNameInput?.focus();
    return;
  }

  const lastInitial = lastInitialInput?.value || '';
  if (!lastInitial || /[^A-Za-z]/.test(lastInitial)) {
    msg.textContent = 'Last initial must be a single letter.';
    msg.className = 'error';
    lastInitialInput?.focus();
    return;
  }

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

  // Validate airport
  if (!airportCodeInput?.value) {
    msg.textContent = 'Please enter a departure airport code (e.g. SFO).';
    msg.className = 'error';
    airportCodeInput?.focus();
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

    msg.textContent = 'Carpool created! Others can now find and join your party.';
    msg.className = '';
    form.reset();
    if (airlineNameInput) airlineNameInput.value = '';
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
