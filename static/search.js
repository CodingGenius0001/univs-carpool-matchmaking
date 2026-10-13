const searchForm = document.querySelector('#search-form');
const results = document.querySelector('#results');
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

// Auto-format inputs
const searchAirport = searchForm?.querySelector('[name="airport_code"]');
const searchDate = searchForm?.querySelector('[name="departure_date"]');
const searchFlight = searchForm?.querySelector('[name="flight_code"]');

searchAirport?.addEventListener('input', () => {
  searchAirport.value = searchAirport.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
});

searchFlight?.addEventListener('input', () => {
  searchFlight.value = searchFlight.value.toUpperCase().replace(/\s+/g, '');
});

const directionSelect = document.getElementById('search-direction');

function selectedDirection() {
  return directionSelect?.value === 'from_airport' ? 'from_airport' : 'to_airport';
}

function applyDirectionText() {
  const from = selectedDirection() === 'from_airport';
  searchForm?.querySelectorAll('[data-to]').forEach((el) => { el.textContent = el.dataset[from ? 'from' : 'to']; });
  directionSelect?.classList.toggle('trip-from', from);
  directionSelect?.classList.toggle('trip-to', !from);
}

results?.addEventListener('click', (e) => {
  const btn = e.target.closest('#switch-direction-btn');
  if (!btn || !directionSelect) return;
  directionSelect.value = btn.dataset.direction;
  // The change handler re-runs the search.
  directionSelect.dispatchEvent(new Event('change'));
});

const dateLabel = searchForm?.querySelector('[name="departure_date"]')?.closest('label');

function showDateError() {
  dateLabel?.classList.add('field-invalid');
  results.innerHTML = `
    <div class="message message-error mt-2" role="alert">
      Pick a date to search by time. The time finds carpools leaving within 2 hours of it on that day.
    </div>`;
  (dateLabel?.querySelector('.us-date-text') || dateLabel?.querySelector('input'))?.focus();
}

function clearDateError() {
  dateLabel?.classList.remove('field-invalid');
}

dateLabel?.addEventListener('input', clearDateError);
dateLabel?.addEventListener('change', clearDateError);

directionSelect?.addEventListener('change', () => {
  applyDirectionText();
  // Re-run an existing search so results always match the chosen direction.
  if (results.children.length) searchForm.requestSubmit();
});
applyDirectionText();


function buildAddUrl() {
  const params = new URLSearchParams();
  params.set('direction', selectedDirection());
  const fc = searchForm?.querySelector('[name="flight_code"]')?.value;
  const ac = searchForm?.querySelector('[name="airport_code"]')?.value;
  const dd = searchForm?.querySelector('[name="departure_date"]')?.value;
  if (fc) params.set('flight_code', fc);
  if (ac) params.set('airport_code', ac);
  if (dd) params.set('departure_date', dd);
  return '/create-a-carpool' + (params.toString() ? '?' + params.toString() : '');
}

// Phone formatting helper
function formatPhone(raw) {
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
}

function formatTime12h(value) {
  const [hour, minute] = String(value || '').split(':');
  const hourNum = Number(hour);
  if (!value || minute === undefined || Number.isNaN(hourNum)) return '\u2014';
  const suffix = hourNum >= 12 ? 'PM' : 'AM';
  return `${((hourNum + 11) % 12) + 1}:${minute} ${suffix}`;
}

// Cached user phone for auto-fill
let cachedUserPhone = '';

// Load user phone on page load
async function loadUserPhone() {
  try {
    const res = await fetch('/api/user/profile');
    const data = await res.json();
    if (data.profile?.phone) {
      cachedUserPhone = data.profile.phone;
    }
  } catch {}
}
loadUserPhone();

function renderResults(data) {
  results.innerHTML = `<p class="text-muted text-sm mt-2">${escapeHtml(data.count)} carpool${data.count !== 1 ? 's' : ''} found</p>` +
    data.results.map((r) => {
      const scoreClass = r.match_score >= 70 ? 'score-high' : r.match_score >= 30 ? 'score-medium' : 'score-low';
      const creatorName = `${r.first_name || ''} ${r.last_initial ? `${r.last_initial}.` : ''}`.trim();
      const reasons = (r.match_reasons || []).map(reason =>
        `<span class="match-tag">${escapeHtml(reason)}</span>`
      ).join('');
      const entryId = Number.isFinite(Number(r.id)) ? String(Number(r.id)) : '';

      const seatsRemaining = r.seats_remaining ?? r.seats_available;
      const isMember = r.is_member;
      const isFull = seatsRemaining <= 0 && !isMember;

      let actionBtn;
      if (r.is_creator) {
        // Organizers leave from My Carpool, where they can pick the next organizer.
        actionBtn = `<a href="/my-party" class="btn btn-primary btn-sm">Manage My Carpool</a>`;
      } else if (isMember) {
        actionBtn = `<button data-id="${entryId}" class="leave-carpool-btn btn btn-danger btn-sm">Leave Carpool</button>
                      <a href="/my-party" class="btn btn-primary btn-sm">View My Carpool</a>`;
      } else if (isFull) {
        actionBtn = `<button class="btn btn-secondary btn-sm" disabled>Carpool Full</button>`;
      } else {
        actionBtn = `<button data-id="${entryId}" class="join-carpool-btn btn btn-primary btn-sm">Join Carpool</button>`;
      }

      return `
        <article class="result trip-card ${Trip.tripClass(r)}">
          <span class="trip-stripe" aria-hidden="true"></span>
          <div class="result-header">
            <div>
              <div class="result-name">${escapeHtml(Trip.headline(r))}</div>
              <p class="trip-subline">
                ${Trip.countdown(r.requested_flight_date) ? `<span class="trip-countdown">${escapeHtml(Trip.countdown(r.requested_flight_date))}</span>` : ''}
                <span>Organized by ${escapeHtml(creatorName)}</span>
              </p>
              ${reasons ? `<div class="match-reasons">${reasons}</div>` : ''}
            </div>
            ${r.match_score > 0 ? `<span class="result-badge ${scoreClass}">${escapeHtml(r.match_score)}% match</span>` : ''}
          </div>
          <div class="result-info">
            <span>
              <span class="label">${(r.flights || []).length > 1 ? 'Flights' : 'Flight'}</span>
              <strong>${escapeHtml((r.flights && r.flights.length ? r.flights : [r.flight_code]).join(' · '))}</strong>
            </span>
            <span>
              <span class="label">Airport</span>
              ${escapeHtml(r.airport_code)}
            </span>
            <span>
              <span class="label">Date</span>
              ${escapeHtml(String(r.requested_flight_date || r.flight_date_utc || '\u2014').replace(/-/g, '/'))}
            </span>
            <span>
              <span class="label">${r.direction === 'from_airport' ? 'Meet at airport' : 'Leave campus'}</span>
              ${escapeHtml(formatTime12h(r.planned_departure_time))}
            </span>
            <span>
              <span class="label">Seats</span>
              ${isFull ? '<span style="color:var(--danger)">Full</span>' : `${escapeHtml(seatsRemaining)} left`}
            </span>
            <span>
              <span class="label">Carpool</span>
              ${escapeHtml(r.member_count || 1)} member${(r.member_count || 1) !== 1 ? 's' : ''}
            </span>
          </div>
          <div class="result-actions">
            ${actionBtn}
          </div>
        </article>`;
    }).join('');

  results.innerHTML += `
    <div class="text-center mt-3">
      <p class="text-muted text-sm">Can't find what you're looking for?</p>
      <a href="${escapeAttr(buildAddUrl())}" class="btn btn-primary mt-1">Create a Carpool</a>
    </div>`;
}

searchForm?.addEventListener('submit', async (e) => {
  e.preventDefault();

  const submitBtn = searchForm.querySelector('button[type="submit"]');

  // Validate at least 1 field is filled
  const fc = searchForm.querySelector('[name="flight_code"]')?.value.trim();
  const ac = searchForm.querySelector('[name="airport_code"]')?.value.trim();
  const dd = searchForm.querySelector('[name="departure_date"]')?.value.trim();
  const pt = searchForm.querySelector('[name="planned_leave_time"]')?.value.trim();
  clearDateError();
  if (!fc && !ac && !dd && !pt) {
    results.innerHTML = `
      <div class="message message-error mt-2" role="alert">
        Enter a flight code, airport code, date, or time to search.
      </div>`;
    return;
  }
  // A time window only makes sense on a given day.
  if (pt && !dd) {
    showDateError();
    return;
  }

  results.innerHTML = '<p class="text-muted text-center mt-2"><span class="spinner"></span> Searching...</p>';
  submitBtn.disabled = true;

  try {
    const formData = Object.fromEntries(new FormData(searchForm).entries());
    if (formData.departure_date && formData.departure_date.includes('-')) {
      const parts = formData.departure_date.split('-');
      if (parts.length === 3 && parts[0].length === 4) {
        formData.departure_date = `${parts[1]}-${parts[2]}-${parts[0]}`;
      }
    }
    const query = new URLSearchParams(formData);
    const res = await fetch(`/api/carpools/search?${query.toString()}`);
    const data = await res.json();

    if (!res.ok) {
      results.innerHTML = `<div class="message message-error mt-2" role="alert">${escapeHtml(data.error || 'Search failed. Please try again.')}</div>`;
      return;
    }

    if (!data.results?.length) {
      // Nothing this way, but something the other way: say so, since the
      // Trip field defaults to Campus -> Airport and is easy to overlook.
      const other = Number(data.other_direction_count) || 0;
      const otherDir = selectedDirection() === 'from_airport' ? 'to_airport' : 'from_airport';
      // Spelled out in words here (no arrows): "from UCR to the airport".
      const inWords = (dir) => (dir === 'from_airport' ? `from the airport to ${Trip.CAMPUS}` : `from ${Trip.CAMPUS} to the airport`);
      const otherHint = other ? `
          <div class="message message-info mt-2 other-direction-hint">
            <span>We didn't find any matches for your search, but we found ${other} carpool${other === 1 ? '' : 's'} going ${escapeHtml(inWords(otherDir))}.</span>
            <button type="button" class="btn btn-primary btn-sm" id="switch-direction-btn" data-direction="${otherDir}">Show trips ${escapeHtml(inWords(otherDir))}</button>
          </div>` : '';
      results.innerHTML = `
        <div class="empty-state mt-3">
          <div class="icon" aria-hidden="true">:(</div>
          <p>No carpools going ${escapeHtml(inWords(selectedDirection()))} match your search.</p>
          ${otherHint}
          <a href="${escapeAttr(buildAddUrl())}" class="btn ${other ? 'btn-secondary' : 'btn-primary'} mt-2">Create a Carpool</a>
        </div>`;
      return;
    }

    renderResults(data);
  } catch (err) {
    results.innerHTML = '<p class="text-muted text-center mt-2">Search failed. Please try again.</p>';
  } finally {
    submitBtn.disabled = false;
  }
});

// Join carpool handler - shows phone prompt
document.addEventListener('click', async (e) => {
  const joinBtn = e.target.closest('.join-carpool-btn');
  if (!joinBtn) return;

  const id = joinBtn.dataset.id;

  // Show phone prompt modal
  showPhonePrompt(id, joinBtn);
});

const FLIGHT_PATTERN = /^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$/;

// The flight the student searched for, to pre-fill the join prompt.
function searchedFlight() {
  const value = (searchForm?.querySelector('[name="flight_code"]')?.value || '').toUpperCase().replace(/\s+/g, '');
  return FLIGHT_PATTERN.test(value) ? value : '';
}

function showPhonePrompt(carpoolId, joinBtn) {
  // Remove existing modal if any
  document.getElementById('phone-modal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'phone-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="card modal-card" style="max-width:400px">
      <h3>Join this carpool</h3>
      <p class="text-muted text-sm">Your phone number helps carpool members coordinate. It will be saved for future use.</p>
      <form id="phone-join-form">
        <label class="mt-2">Phone Number
          <input type="tel" id="join-phone" required placeholder="+1 (909) 555 1234" maxlength="17" value="${escapeAttr(cachedUserPhone)}" />
        </label>
        <label class="mt-2"><span>Your Flight Number <span class="label-optional">(optional)</span></span>
          <input type="text" id="join-flight" placeholder="e.g. UA1375" maxlength="8" autocapitalize="characters" autocomplete="off" value="${escapeAttr(searchedFlight())}" />
        </label>
        <p class="text-muted text-sm">Added to the carpool's flight list so everyone can track arrivals. It isn't shown next to your name.</p>
        <div class="modal-actions mt-2">
          <button type="button" class="btn btn-secondary" id="phone-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary">Join Carpool</button>
        </div>
        <p id="phone-error" class="text-sm mt-1" style="color:var(--danger)"></p>
      </form>
    </div>`;
  document.body.appendChild(modal);

  const phoneInput = document.getElementById('join-phone');
  phoneInput.addEventListener('input', () => {
    phoneInput.value = formatPhone(phoneInput.value);
  });
  const flightInput = document.getElementById('join-flight');
  flightInput.addEventListener('input', () => {
    flightInput.value = flightInput.value.toUpperCase().replace(/\s+/g, '');
  });

  // Focus input
  phoneInput.focus();

  document.getElementById('phone-cancel').addEventListener('click', () => {
    modal.remove();
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });

  document.getElementById('phone-join-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const phone = phoneInput.value.trim();
    const phonePattern = /^\+1 \([0-9]{3}\) [0-9]{3} [0-9]{4}$/;
    if (!phonePattern.test(phone)) {
      document.getElementById('phone-error').textContent = 'Please enter a valid phone number in format +1 (XXX) XXX XXXX';
      return;
    }
    const flight = flightInput.value.trim();
    if (flight && !FLIGHT_PATTERN.test(flight)) {
      document.getElementById('phone-error').textContent = 'Flight number should look like UA1375, or leave it blank.';
      flightInput.focus();
      return;
    }

    const submitBtn = modal.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Joining...';
    joinBtn.disabled = true;
    joinBtn.textContent = 'Joining...';

    try {
      const res = await fetch(`/api/carpools/${carpoolId}/join`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify({ phone, flight_code: flight })
      });
      const data = await res.json();
      if (res.ok) {
        modal.remove();
        cachedUserPhone = phone;
        joinBtn.textContent = 'Joined!';
        joinBtn.className = 'btn btn-secondary btn-sm';
        setTimeout(() => searchForm?.requestSubmit(), 500);
        const myPartyBtn = document.getElementById('my-party-btn');
        if (myPartyBtn) myPartyBtn.style.display = '';
        // Also show mobile carpool link
        const mobileLink = document.getElementById('mobile-my-party-link');
        if (mobileLink) mobileLink.style.display = '';
      } else {
        showJoinError(modal, data.error || 'Could not join this carpool.');
        joinBtn.textContent = 'Join Carpool';
        joinBtn.disabled = false;
      }
    } catch {
      showJoinError(modal, 'Network error. Check your connection and try again.');
      joinBtn.textContent = 'Join Carpool';
      joinBtn.disabled = false;
    }
  });
}

// Fill a modal card with a clear error, right where the user is looking.
function renderErrorCard(modal, title, text) {
  const card = modal.querySelector('.modal-card');
  card.innerHTML = `
    <div class="join-error-icon" aria-hidden="true">!</div>
    <h3>${escapeHtml(title)}</h3>
    <p class="join-error-text" role="alert">${escapeHtml(text)}</p>
    <div class="modal-actions mt-2">
      <button type="button" class="btn btn-primary" id="join-error-ok">OK</button>
    </div>`;
  const ok = card.querySelector('#join-error-ok');
  ok.addEventListener('click', () => modal.remove());
  ok.focus();
}

function showJoinError(modal, text) {
  renderErrorCard(modal, "Couldn't join this carpool", text);
}

function showErrorPopup(title, text) {
  document.getElementById('phone-modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'phone-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = '<div class="card modal-card" style="max-width:400px"></div>';
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
  renderErrorCard(modal, title, text);
}

// Same look as the confirm popups on My Carpool; resolves true on confirm.
function confirmDanger({ title, message, confirmLabel, cancelLabel = 'Cancel' }) {
  return new Promise((resolve) => {
    document.getElementById('phone-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'phone-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="card modal-card" style="max-width:400px">
        <h3>${escapeHtml(title)}</h3>
        <p class="modal-copy mt-2">${escapeHtml(message)}</p>
        <div class="modal-actions mt-2">
          <button type="button" class="btn btn-secondary" data-answer="no">${escapeHtml(cancelLabel)}</button>
          <button type="button" class="btn btn-danger" data-answer="yes">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const done = (answer) => { modal.remove(); resolve(answer); };
    modal.addEventListener('click', (e) => {
      if (e.target === modal) return done(false);
      const btn = e.target.closest('[data-answer]');
      if (btn) done(btn.dataset.answer === 'yes');
    });
    modal.querySelector('[data-answer="no"]').focus();
  });
}

// Leave carpool handler
document.addEventListener('click', async (e) => {
  const leaveBtn = e.target.closest('.leave-carpool-btn');
  if (!leaveBtn) return;

  const id = leaveBtn.dataset.id;
  const confirmed = await confirmDanger({
    title: 'Leave this carpool?',
    message: 'You will be removed from this carpool. Leaving still counts toward your limit of 2 joins every 24 hours.',
    confirmLabel: 'Leave carpool',
    cancelLabel: 'Stay',
  });
  if (!confirmed) return;
  leaveBtn.disabled = true;
  leaveBtn.textContent = 'Leaving...';

  try {
    const res = await fetch(`/api/carpools/${id}/leave`, {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
    });
    if (res.ok) {
      leaveBtn.textContent = 'Left!';
      setTimeout(() => searchForm?.requestSubmit(), 500);
    } else {
      const data = await res.json().catch(() => ({}));
      showErrorPopup("Couldn't leave this carpool", data.error || 'Could not leave this carpool.');
      leaveBtn.textContent = 'Leave Carpool';
      leaveBtn.disabled = false;
    }
  } catch {
    showErrorPopup("Couldn't leave this carpool", 'Network error. Check your connection and try again.');
    leaveBtn.textContent = 'Leave Carpool';
    leaveBtn.disabled = false;
  }
});
