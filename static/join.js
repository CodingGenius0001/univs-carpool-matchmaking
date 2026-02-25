const form = document.querySelector('#join-form');
const msg = document.querySelector('#join-message');

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  msg.textContent = 'Submitting and fetching flight details...';
  const payload = Object.fromEntries(new FormData(form).entries());
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
});
