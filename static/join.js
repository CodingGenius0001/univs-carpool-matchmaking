const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const departureDateInput = document.querySelector('#departure_date');
const airlineDropdown = document.querySelector('#airline-dropdown');
const airlineNameInput = document.querySelector('#airline_name');
const seatsInput = document.querySelector('#seats_available');
const plannedDepartureInput = document.querySelector('#planned_departure_time');
const notesInput = document.querySelector('#notes');
const backBtn = document.querySelector('#wizard-back');
const nextBtn = document.querySelector('#wizard-next');
const submitBtn = document.querySelector('#wizard-submit');
const progressText = document.querySelector('#wizard-progress-text');
const progressFill = document.querySelector('#wizard-progress-fill');
const review = document.querySelector('#wizard-review');
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
const steps = Array.from(document.querySelectorAll('.wizard-step'));
const urlParams = new URLSearchParams(window.location.search);

let currentStep = 0;
let airlineActiveIdx = -1;
let airlineResults = [];

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

function setMessage(text, isError = false) {
  if (!msg) return;
  msg.textContent = text;
  msg.className = isError ? 'error' : '';
}

function clearMessage() {
  setMessage('');
}

function focusFirstInput(stepIndex) {
  const target = steps[stepIndex]?.querySelector('input, textarea, select, button');
  target?.focus();
}

function formatDisplayDate(value) {
  if (!value) return 'Not set';
  const [year, month, day] = value.split('-');
  if (!year || !month || !day) return value;
  return `${month}/${day}/${year}`;
}

function formatDisplayTime(value) {
  if (!value) return 'Not set';
  const [hour, minute] = value.split(':');
  if (hour === undefined || minute === undefined) return value;
  const hourNum = Number(hour);
  if (Number.isNaN(hourNum)) return value;
  const suffix = hourNum >= 12 ? 'PM' : 'AM';
  const displayHour = ((hourNum + 11) % 12) + 1;
  return `${displayHour}:${minute} ${suffix}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function updateReview() {
  if (!review) return;
  review.innerHTML = `
    <div class="wizard-review-grid">
      <div class="wizard-review-item">
        <span class="label">Phone</span>
        <strong>${escapeHtml(phoneInput?.value || 'Not set')}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Flight</span>
        <strong>${escapeHtml(flightCodeInput?.value || 'Not set')}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Airline</span>
        <strong>${escapeHtml(airlineNameInput?.value || 'Not set')}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Departure date</span>
        <strong>${escapeHtml(formatDisplayDate(departureDateInput?.value || ''))}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Departure airport</span>
        <strong>${escapeHtml(airportCodeInput?.value || 'Not set')}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Campus departure</span>
        <strong>${escapeHtml(formatDisplayTime(plannedDepartureInput?.value || ''))}</strong>
      </div>
      <div class="wizard-review-item">
        <span class="label">Seats</span>
        <strong>${escapeHtml(seatsInput?.value || '4')}</strong>
      </div>
      <div class="wizard-review-item wizard-review-item-wide">
        <span class="label">Notes</span>
        <strong>${escapeHtml(notesInput?.value?.trim() || 'No extra notes')}</strong>
      </div>
    </div>
  `;
}

function updateWizardUi() {
  steps.forEach((step, index) => {
    step.classList.toggle('is-active', index === currentStep);
  });

  const progressPct = ((currentStep + 1) / steps.length) * 100;
  if (progressText) {
    progressText.textContent = `Step ${currentStep + 1} of ${steps.length}`;
  }
  if (progressFill) {
    progressFill.style.width = `${progressPct}%`;
  }

  if (backBtn) {
    backBtn.hidden = currentStep === 0;
  }
  if (nextBtn) {
    nextBtn.hidden = currentStep === steps.length - 1;
    nextBtn.textContent = currentStep === steps.length - 2 ? 'Review' : 'Next';
  }
  if (submitBtn) {
    submitBtn.hidden = currentStep !== steps.length - 1;
  }

  if (currentStep === steps.length - 1) {
    updateReview();
  }
}

function showStep(stepIndex) {
  currentStep = Math.max(0, Math.min(stepIndex, steps.length - 1));
  updateWizardUi();
  clearMessage();
  focusFirstInput(currentStep);
}

function validateDateInput() {
  const val = departureDateInput?.value;
  if (!val) {
    setMessage('Please select a departure date from the calendar.', true);
    departureDateInput?.focus();
    return false;
  }

  const selected = new Date(`${val}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  if (selected < today) {
    departureDateInput.value = '';
    setMessage('Departure date cannot be in the past.', true);
    departureDateInput.focus();
    return false;
  }

  departureDateInput.setCustomValidity('');
  return true;
}

function validateCurrentStep() {
  clearMessage();

  if (currentStep === 0) {
    const phonePattern = /^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$/;
    if (!phonePattern.test(phoneInput?.value || '')) {
      setMessage('Please enter a valid phone number in format +1 (XXX) XXX XXXX.', true);
      phoneInput?.focus();
      return false;
    }
    return true;
  }

  if (currentStep === 1) {
    const flightCode = String(flightCodeInput?.value || '').toUpperCase().replace(/\s+/g, '');
    flightCodeInput.value = flightCode;
    if (!flightCode) {
      setMessage('Please enter your flight code.', true);
      flightCodeInput?.focus();
      return false;
    }
    if (!/^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$/.test(flightCode)) {
      setMessage('Flight code format should look like UA533 or AA1024.', true);
      flightCodeInput?.focus();
      return false;
    }
    return true;
  }

  if (currentStep === 2) {
    if (!validateDateInput()) {
      return false;
    }
    if (!airportCodeInput?.value) {
      setMessage('Please enter a departure airport code like SFO.', true);
      airportCodeInput?.focus();
      return false;
    }
    if ((airportCodeInput.value || '').length !== 3) {
      setMessage('Airport code must be exactly 3 letters.', true);
      airportCodeInput?.focus();
      return false;
    }
    return true;
  }

  if (currentStep === 3) {
    const seats = Number(seatsInput?.value || 4);
    if (!Number.isInteger(seats) || seats < 1 || seats > 7) {
      setMessage('Total seats must be a whole number between 1 and 7.', true);
      seatsInput?.focus();
      return false;
    }
    if (plannedDepartureInput?.value && !/^\d{2}:\d{2}$/.test(plannedDepartureInput.value)) {
      setMessage('Planned departure time must use HH:MM format.', true);
      plannedDepartureInput?.focus();
      return false;
    }
    return true;
  }

  return true;
}

function closeAirlineDropdown() {
  if (airlineDropdown) {
    airlineDropdown.classList.remove('open');
    airlineDropdown.innerHTML = '';
  }
  airlineActiveIdx = -1;
  airlineResults = [];
}

function updateActiveAirline() {
  const opts = airlineDropdown?.querySelectorAll('.airline-option') || [];
  opts.forEach((opt, i) => opt.classList.toggle('active', i === airlineActiveIdx));
}

function showAirlineDropdown(matches) {
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
    const codeSpan = document.createElement('span');
    codeSpan.className = 'code';
    codeSpan.textContent = airline.code || '';
    const nameSpan = document.createElement('span');
    nameSpan.className = 'name';
    nameSpan.textContent = airline.name || '';
    opt.append(codeSpan, nameSpan);
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
}

async function fetchAirlineSuggestions(prefix) {
  try {
    const res = await fetch(`/api/airlines/suggest?q=${encodeURIComponent(prefix)}`);
    const data = await res.json();
    return data.results || [];
  } catch {
    return [];
  }
}

async function hydrateUserProfile() {
  try {
    const res = await fetch('/api/user/profile');
    const data = await res.json();
    if (res.ok && data.profile?.phone && !phoneInput?.value) {
      phoneInput.value = data.profile.phone;
    }
  } catch {
    // Ignore profile hydration failures.
  }
}

function applyUrlPrefill() {
  if (urlParams.get('flight_code') && flightCodeInput) {
    flightCodeInput.value = urlParams.get('flight_code');
  }
  if (urlParams.get('airport_code') && airportCodeInput) {
    airportCodeInput.value = urlParams.get('airport_code');
  }
  if (urlParams.get('departure_date') && departureDateInput) {
    departureDateInput.value = urlParams.get('departure_date');
  }
  if (flightCodeInput?.value) {
    flightCodeInput.dispatchEvent(new Event('input'));
  }
}

async function submitCarpool() {
  closeAirlineDropdown();

  if (!csrfToken) {
    setMessage('Security token missing. Please refresh the page and try again.', true);
    return;
  }

  if (!validateDateInput()) {
    showStep(2);
    return;
  }

  setMessage('Saving your flight details...');

  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span class="spinner"></span> Saving...';
  if (backBtn) backBtn.disabled = true;

  const payload = Object.fromEntries(new FormData(form).entries());
  payload.flight_code = String(payload.flight_code || '').toUpperCase().replace(/\s+/g, '');
  payload.csrf_token = csrfToken;

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
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(data.error || 'Could not submit. Please check your inputs.', true);
      return;
    }

    setMessage('Carpool created! Redirecting to your carpool...');
    form.reset();
    if (airlineNameInput) airlineNameInput.value = '';
    const myCarPoolBtn = document.getElementById('my-party-btn');
    if (myCarPoolBtn) myCarPoolBtn.style.display = '';
    const mobileCarPoolLink = document.getElementById('mobile-my-party-link');
    if (mobileCarPoolLink) mobileCarPoolLink.style.display = '';
    setTimeout(() => { window.location.href = '/my-party'; }, 1200);
  } catch (err) {
    if (err.name === 'AbortError') {
      setMessage('Request timed out. Please try again.', true);
    } else {
      setMessage('Network error. Please check your connection and try again.', true);
    }
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
    if (backBtn) backBtn.disabled = false;
  }
}

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

airportCodeInput?.addEventListener('input', () => {
  airportCodeInput.value = airportCodeInput.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

phoneInput?.addEventListener('input', () => {
  phoneInput.value = formatPhone(phoneInput.value);
});

departureDateInput?.addEventListener('change', () => {
  if (departureDateInput.value) {
    validateDateInput();
  }
});

flightCodeInput?.addEventListener('input', async () => {
  const raw = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
  flightCodeInput.value = raw;

  if (!raw && airlineNameInput) {
    airlineNameInput.value = '';
  }

  if (raw.length >= 1 && raw.length <= 3) {
    const matches = await fetchAirlineSuggestions(raw);
    if (matches.length) {
      showAirlineDropdown(matches);
      if (airlineNameInput) {
        const exact = matches.find((m) => m.code === raw);
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
    if (airlineNameInput && raw.length >= 3) {
      let resolved = false;
      for (const len of [3, 2]) {
        if (raw.length < len) continue;
        const prefix = raw.slice(0, len);
        const matches = await fetchAirlineSuggestions(prefix);
        const exact = matches.find((m) => m.code === prefix);
        if (exact) {
          airlineNameInput.value = exact.name;
          resolved = true;
          break;
        }
      }
      if (!resolved) {
        airlineNameInput.value = '';
      }
    }
  }
});

flightCodeInput?.addEventListener('keydown', (e) => {
  if (!airlineDropdown?.classList.contains('open') || !airlineResults.length) {
    if (e.key === 'Enter' && currentStep === 1) {
      e.preventDefault();
      if (validateCurrentStep()) {
        showStep(currentStep + 1);
      }
    }
    return;
  }

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

flightCodeInput?.addEventListener('blur', () => {
  setTimeout(closeAirlineDropdown, 150);
});

form?.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const target = e.target;
  if (target instanceof HTMLTextAreaElement) return;
  if (target === submitBtn || target === backBtn || target === nextBtn) return;
  if (target === flightCodeInput && airlineDropdown?.classList.contains('open')) return;

  e.preventDefault();
  if (currentStep < steps.length - 1) {
    if (validateCurrentStep()) {
      showStep(currentStep + 1);
    }
  } else {
    form.requestSubmit();
  }
});

nextBtn?.addEventListener('click', () => {
  closeAirlineDropdown();
  if (!validateCurrentStep()) {
    return;
  }
  showStep(currentStep + 1);
});

backBtn?.addEventListener('click', () => {
  closeAirlineDropdown();
  showStep(currentStep - 1);
});

form?.addEventListener('submit', async (e) => {
  e.preventDefault();

  for (let stepIndex = 0; stepIndex < steps.length - 1; stepIndex += 1) {
    currentStep = stepIndex;
    if (!validateCurrentStep()) {
      updateWizardUi();
      return;
    }
  }

  currentStep = steps.length - 1;
  updateWizardUi();
  await submitCarpool();
});

hydrateUserProfile();
applyUrlPrefill();
updateWizardUi();
