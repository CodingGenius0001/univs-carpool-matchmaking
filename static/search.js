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
  return '/add-flight-details' + (params.toString() ? '?' + params.toString() : '');
}

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
                      <a href="/my-party" class="btn btn-primary btn-sm" style="margin-left:0.5rem;">View My Party</a>`;
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
  results.innerHTML = '<p class="text-muted text-center mt-2"><span class="spinner"></span> Searching...</p>';

  const submitBtn = searchForm.querySelector('button[type="submit"]');
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
      const addUrl = '/add-flight-details' + (params.toString() ? '?' + params.toString() : '');
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

// Join party handler
document.addEventListener('click', async (e) => {
  const joinBtn = e.target.closest('.join-party-btn');
  if (!joinBtn) return;

  const id = joinBtn.dataset.id;
  joinBtn.disabled = true;
  joinBtn.textContent = 'Joining...';

  try {
    const res = await fetch(`/api/carpools/${id}/join`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      joinBtn.textContent = 'Joined!';
      joinBtn.className = 'btn btn-secondary btn-sm';
      // Re-trigger search to refresh the UI
      setTimeout(() => searchForm?.requestSubmit(), 500);
      // Show My Party button in header if hidden
      const myPartyBtn = document.getElementById('my-party-btn');
      if (myPartyBtn) myPartyBtn.style.display = '';
    } else {
      joinBtn.textContent = data.error || 'Failed';
      setTimeout(() => { joinBtn.textContent = 'Join Party'; joinBtn.disabled = false; }, 2000);
    }
  } catch {
    joinBtn.textContent = 'Error';
    setTimeout(() => { joinBtn.textContent = 'Join Party'; joinBtn.disabled = false; }, 2000);
  }
});

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
