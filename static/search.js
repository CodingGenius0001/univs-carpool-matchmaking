const searchForm = document.querySelector('#search-form');
const results = document.querySelector('#results');

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

function buildAddUrl() {
  const params = new URLSearchParams();
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
  results.innerHTML = `<p class="text-muted text-sm mt-2">${data.count} carpool${data.count !== 1 ? 's' : ''} found</p>` +
    data.results.map((r) => {
      const scoreClass = r.match_score >= 70 ? 'score-high' : r.match_score >= 30 ? 'score-medium' : 'score-low';
      const reasons = (r.match_reasons || []).map(reason =>
        `<span class="match-tag">${reason}</span>`
      ).join('');

      const seatsRemaining = r.seats_remaining ?? r.seats_available;
      const isMember = r.is_member;
      const isFull = seatsRemaining <= 0 && !isMember;

      let actionBtn;
      if (isMember) {
        actionBtn = `<button data-id="${r.id}" class="leave-party-btn btn btn-secondary btn-sm">Leave Party</button>
                      <a href="/my-party" class="btn btn-primary btn-sm" style="margin-left:0.5rem;">View My Carpool</a>`;
      } else if (isFull) {
        actionBtn = `<button class="btn btn-secondary btn-sm" disabled>Party Full</button>`;
      } else {
        actionBtn = `<button data-id="${r.id}" class="join-party-btn btn btn-primary btn-sm">Join Party</button>`;
      }

      return `
        <article class="result">
          <div class="result-header">
            <div>
              <div class="result-name">${r.first_name} ${r.last_initial}.</div>
              ${reasons ? `<div class="match-reasons">${reasons}</div>` : ''}
            </div>
            ${r.match_score > 0 ? `<span class="result-badge ${scoreClass}">${r.match_score}% match</span>` : ''}
          </div>
          <div class="result-info">
            <span>
              <span class="label">Flight</span>
              <strong>${r.flight_code}</strong>
            </span>
            <span>
              <span class="label">Airport</span>
              ${r.airport_code}
            </span>
            <span>
              <span class="label">Date</span>
              ${r.requested_flight_date || r.flight_date_utc || '\u2014'}
            </span>
            <span>
              <span class="label">Seats</span>
              ${isFull ? '<span style="color:var(--danger)">Full</span>' : `${seatsRemaining} left`}
            </span>
            <span>
              <span class="label">Party</span>
              ${r.member_count || 1} member${(r.member_count || 1) !== 1 ? 's' : ''}
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
      <a href="${buildAddUrl()}" class="btn btn-primary mt-1">Create a Carpool</a>
    </div>`;
}

searchForm?.addEventListener('submit', async (e) => {
  e.preventDefault();

  const submitBtn = searchForm.querySelector('button[type="submit"]');

  // Validate at least 1 field is filled
  const fc = searchForm.querySelector('[name="flight_code"]')?.value.trim();
  const ac = searchForm.querySelector('[name="airport_code"]')?.value.trim();
  const dd = searchForm.querySelector('[name="departure_date"]')?.value.trim();
  if (!fc && !ac && !dd) {
    results.innerHTML = `
      <div class="message message-warning mt-2">
        At least 1 search field is required. Please enter a flight code, airport code, or departure date to search.
      </div>`;
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

    if (!data.results?.length) {
      const params = new URLSearchParams();
      if (formData.flight_code) params.set('flight_code', searchForm.querySelector('[name="flight_code"]').value);
      if (formData.airport_code) params.set('airport_code', searchForm.querySelector('[name="airport_code"]').value);
      if (searchForm.querySelector('[name="departure_date"]').value) params.set('departure_date', searchForm.querySelector('[name="departure_date"]').value);
      const addUrl = '/create-a-carpool' + (params.toString() ? '?' + params.toString() : '');
      results.innerHTML = `
        <div class="empty-state mt-3">
          <div class="icon">0</div>
          <p>No matching carpools found. Try adjusting your search or create your own carpool!</p>
          <a href="${buildAddUrl()}" class="btn btn-primary mt-2">Create a Carpool</a>
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

// Join party handler - shows phone prompt
document.addEventListener('click', async (e) => {
  const joinBtn = e.target.closest('.join-party-btn');
  if (!joinBtn) return;

  const id = joinBtn.dataset.id;

  // Show phone prompt modal
  showPhonePrompt(id, joinBtn);
});

function showPhonePrompt(carpoolId, joinBtn) {
  // Remove existing modal if any
  document.getElementById('phone-modal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'phone-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="card modal-card" style="max-width:400px">
      <h3>Enter Your Phone Number</h3>
      <p class="text-muted text-sm">Your phone number helps party members coordinate. It will be saved for future use.</p>
      <form id="phone-join-form">
        <label class="mt-2">Phone Number
          <input type="tel" id="join-phone" required placeholder="+1 (909) 555 1234" maxlength="17" value="${cachedUserPhone}" />
        </label>
        <label class="mt-2" style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;">
          <input type="checkbox" id="sms-opt-in" checked style="width:auto;" />
          <span>&#10003; Send me text updates for this carpool</span>
        </label>
        <div class="modal-actions mt-2">
          <button type="submit" class="btn btn-primary">Join Party</button>
          <button type="button" class="btn btn-secondary" id="phone-cancel">Cancel</button>
        </div>
        <p id="phone-error" class="text-sm mt-1" style="color:var(--danger)"></p>
      </form>
    </div>`;
  document.body.appendChild(modal);

  const phoneInput = document.getElementById('join-phone');
  phoneInput.addEventListener('input', () => {
    phoneInput.value = formatPhone(phoneInput.value);
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

    joinBtn.disabled = true;
    joinBtn.textContent = 'Joining...';
    modal.remove();

    try {
      const res = await fetch(`/api/carpools/${carpoolId}/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, sms_opt_in: document.getElementById('sms-opt-in').checked })
      });
      const data = await res.json();
      if (res.ok) {
        cachedUserPhone = phone;
        joinBtn.textContent = 'Joined!';
        joinBtn.className = 'btn btn-secondary btn-sm';
        setTimeout(() => searchForm?.requestSubmit(), 500);
        const myPartyBtn = document.getElementById('my-party-btn');
        if (myPartyBtn) myPartyBtn.style.display = '';
        // Also show mobile party link
        const mobileLink = document.getElementById('mobile-my-party-link');
        if (mobileLink) mobileLink.style.display = '';
      } else {
        joinBtn.textContent = data.error || 'Failed';
        setTimeout(() => { joinBtn.textContent = 'Join Party'; joinBtn.disabled = false; }, 2000);
      }
    } catch {
      joinBtn.textContent = 'Error';
      setTimeout(() => { joinBtn.textContent = 'Join Party'; joinBtn.disabled = false; }, 2000);
    }
  });
}

// Leave party handler
document.addEventListener('click', async (e) => {
  const leaveBtn = e.target.closest('.leave-party-btn');
  if (!leaveBtn) return;

  const id = leaveBtn.dataset.id;
  leaveBtn.disabled = true;
  leaveBtn.textContent = 'Leaving...';

  try {
    const res = await fetch(`/api/carpools/${id}/leave`, { method: 'POST' });
    if (res.ok) {
      leaveBtn.textContent = 'Left!';
      setTimeout(() => searchForm?.requestSubmit(), 500);
    } else {
      const data = await res.json();
      leaveBtn.textContent = data.error || 'Failed';
      setTimeout(() => { leaveBtn.textContent = 'Leave Party'; leaveBtn.disabled = false; }, 2000);
    }
  } catch {
    leaveBtn.textContent = 'Error';
    setTimeout(() => { leaveBtn.textContent = 'Leave Party'; leaveBtn.disabled = false; }, 2000);
  }
});
