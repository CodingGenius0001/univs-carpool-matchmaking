const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');
const airportCodeInput = document.querySelector('#airport_code');
const phoneInput = document.querySelector('#phone');
const flightCodeInput = document.querySelector('#flight_code');
const suggestionsBox = document.querySelector('#flight_suggestions');

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

airportCodeInput?.addEventListener('input', () => {
  airportCodeInput.value = airportCodeInput.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

phoneInput?.addEventListener('input', () => {
  phoneInput.value = formatPhone(phoneInput.value);
});

flightCodeInput?.addEventListener('input', async () => {
  flightCodeInput.value = flightCodeInput.value.toUpperCase().replace(/\s+/g, '');
  const q = flightCodeInput.value;
  if (q.length < 2) {
    suggestionsBox.innerHTML = '';
    return;
  }

  const res = await fetch(`/api/flights/suggest?query=${encodeURIComponent(q)}`);
  const data = await res.json();
  suggestionsBox.innerHTML = '';

  if (!data.results?.length) {
    const opt = document.createElement('option');
    opt.textContent = 'No live suggestions found yet';
    opt.disabled = true;
    suggestionsBox.appendChild(opt);
    return;
  }

  data.results.forEach((flight) => {
    const opt = document.createElement('option');
    opt.value = flight.flight_code;
    opt.textContent = `${flight.flight_code} | ${flight.time_utc} UTC | ${flight.departure} → ${flight.destination}`;
    suggestionsBox.appendChild(opt);
  });
});

suggestionsBox?.addEventListener('change', () => {
  if (suggestionsBox.value) {
    flightCodeInput.value = suggestionsBox.value;
  }
});

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  msg.textContent = 'Submitting and fetching flight details...';

  const payload = Object.fromEntries(new FormData(form).entries());
  payload.flight_code = String(payload.flight_code || '').toUpperCase().replace(/\s+/g, '');

  const res = await fetch('/api/carpools', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  const data = await res.json();
  if (!res.ok) {
    msg.textContent = `Error: ${data.error || 'Could not submit.'}`;
    return;
  }

  msg.textContent = `Saved. Flight info fetched via ${data.entry.fetched_from}.`;
  form.reset();
  suggestionsBox.innerHTML = '';
});
