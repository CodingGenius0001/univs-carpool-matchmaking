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

// Convert YYYY-MM-DD from date picker to MM-DD-YYYY for the API on form submit
// (native date input handles formatting)

searchForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  results.innerHTML = '<p class="text-muted text-center mt-2"><span class="spinner"></span> Searching...</p>';

  const submitBtn = searchForm.querySelector('button[type="submit"]');
  submitBtn.disabled = true;

  try {
    const formData = Object.fromEntries(new FormData(searchForm).entries());
    // Convert YYYY-MM-DD from date picker to MM-DD-YYYY for the API
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
          <p>No matching carpools found. Try adjusting your search or add your own flight so others can find you!</p>
          <a href="${addUrl}" class="btn btn-primary mt-2">Add Your Flight Details</a>
        </div>`;
      return;
    }

    results.innerHTML = `<p class="text-muted text-sm mt-2">${data.count} carpool${data.count !== 1 ? 's' : ''} found</p>` +
      data.results.map((r) => {
        const scoreClass = r.match_score >= 70 ? 'score-high' : r.match_score >= 30 ? 'score-medium' : 'score-low';
        const reasons = (r.match_reasons || []).map(reason =>
          `<span class="match-tag">${reason}</span>`
        ).join('');

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
                ${r.seats_available}
              </span>
              <span>
                <span class="label">Status</span>
                ${r.status}
              </span>
            </div>
            <div class="result-actions">
              <button data-id="${r.id}" class="view-more btn btn-secondary btn-sm">View Contact Info</button>
            </div>
            <div id="detail-${r.id}" class="result-detail" style="display:none;"></div>
          </article>`;
      }).join('');

    // Add "Add Your Flight Details" button below results
    const addParams = new URLSearchParams();
    if (formData.flight_code) addParams.set('flight_code', searchForm.querySelector('[name="flight_code"]').value);
    if (formData.airport_code) addParams.set('airport_code', searchForm.querySelector('[name="airport_code"]').value);
    if (searchForm.querySelector('[name="departure_date"]').value) addParams.set('departure_date', searchForm.querySelector('[name="departure_date"]').value);
    const addLink = '/add-flight-details' + (addParams.toString() ? '?' + addParams.toString() : '');
    results.innerHTML += `
      <div class="text-center mt-3">
        <p class="text-muted text-sm">Can't find what you're looking for?</p>
        <a href="${addLink}" class="btn btn-primary mt-1">Add Your Flight Details</a>
      </div>`;
  } catch (err) {
    results.innerHTML = '<p class="text-muted text-center mt-2">Search failed. Please try again.</p>';
  } finally {
    submitBtn.disabled = false;
  }
});

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.view-more');
  if (!btn) return;

  const id = btn.dataset.id;
  const detail = document.querySelector(`#detail-${id}`);
  if (!detail) return;

  if (detail.style.display !== 'none') {
    detail.style.display = 'none';
    btn.textContent = 'View Contact Info';
    return;
  }

  btn.textContent = 'Loading...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/carpools/${id}`);
    const data = await res.json();
    if (!res.ok) {
      detail.innerHTML = '<span class="text-muted">Unable to load contact info.</span>';
      detail.style.display = 'block';
      return;
    }

    detail.innerHTML = `
      <div class="result-info">
        <span>
          <span class="label">Phone</span>
          <strong>${data.entry.phone}</strong>
        </span>
        <span>
          <span class="label">Airport</span>
          ${data.entry.airport_name || '\u2014'}
        </span>
        <span>
          <span class="label">Notes</span>
          ${data.entry.notes || 'No notes provided'}
        </span>
      </div>`;
    detail.style.display = 'block';
    btn.textContent = 'Hide Contact Info';
  } catch {
    detail.innerHTML = '<span class="text-muted">Failed to load. Try again.</span>';
    detail.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
});
