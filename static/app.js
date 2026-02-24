const carpoolForm = document.querySelector('#carpool-form');
const searchForm = document.querySelector('#search-form');
const formMessage = document.querySelector('#form-message');
const results = document.querySelector('#results');

carpoolForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  formMessage.textContent = 'Saving...';

  const payload = Object.fromEntries(new FormData(carpoolForm).entries());

  const response = await fetch('/api/carpools', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  const data = await response.json();
  formMessage.textContent = response.ok
    ? `Saved! Listing #${data.entry.id} created for ${data.entry.first_name}.`
    : `Error: ${data.error || 'Could not save listing.'}`;

  if (response.ok) carpoolForm.reset();
});

searchForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  results.innerHTML = '<p>Searching...</p>';

  const query = new URLSearchParams(Object.fromEntries(new FormData(searchForm).entries()));
  const response = await fetch(`/api/carpools/search?${query.toString()}`);
  const data = await response.json();

  if (!data.results?.length) {
    results.innerHTML = '<p>No carpools found yet. Try broader filters.</p>';
    return;
  }

  results.innerHTML = data.results
    .map((entry) => `
      <article class="result">
        <strong>${entry.first_name} ${entry.last_initial}.</strong>
        <div>${entry.flight_number} • ${entry.flight_date} ${entry.flight_time}</div>
        <div>${entry.airport_name} (${entry.airport_location})</div>
        <div>Seats: ${entry.seats_available} • Match score: ${entry.match_score}</div>
        <small>${entry.match_reasons.join(', ')}</small>
      </article>
    `)
    .join('');
});
