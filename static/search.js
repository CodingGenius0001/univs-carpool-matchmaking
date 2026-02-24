const searchForm = document.querySelector('#search-form');
const results = document.querySelector('#results');

searchForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  results.innerHTML = '<p>Searching...</p>';
  const query = new URLSearchParams(Object.fromEntries(new FormData(searchForm).entries()));
  const res = await fetch(`/api/carpools/search?${query.toString()}`);
  const data = await res.json();

  if (!data.results?.length) {
    results.innerHTML = '<p>No matches found.</p>';
    return;
  }

  results.innerHTML = data.results.map((r) => `
    <article class="result">
      <strong>${r.first_name} ${r.last_initial}.</strong>
      <div>${r.flight_code} → ${r.airport_code}</div>
      <div>${r.airport_name} (${r.airport_location})</div>
      <div>Status: ${r.status} | Score: ${r.match_score}</div>
      <button data-id="${r.id}" class="view-more">View More Info</button>
      <div id="detail-${r.id}"></div>
    </article>
  `).join('');
});

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.view-more');
  if (!btn) return;
  const id = btn.dataset.id;
  const detail = document.querySelector(`#detail-${id}`);
  detail.textContent = 'Loading contact...';
  const res = await fetch(`/api/carpools/${id}`);
  const data = await res.json();
  if (!res.ok) {
    detail.textContent = 'Unable to load.';
    return;
  }
  detail.innerHTML = `<small>Phone: ${data.entry.phone}<br/>Notes: ${data.entry.notes || 'N/A'}</small>`;
});
