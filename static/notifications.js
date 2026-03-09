// Notification popup — checks for unread notifications on page load
// and shows a dismissible modal popup for each one.
(function () {
  const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || '';
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Inject modal HTML into body
  const modalHtml = `
    <div id="notif-popup" class="modal-overlay" style="display:none;z-index:1100;">
      <div class="card modal-card" style="max-width:500px;">
        <h3 style="margin-bottom:0.75rem;">Notifications</h3>
        <div id="notif-popup-list"></div>
        <div class="modal-actions mt-2">
          <button type="button" class="btn btn-secondary" id="notif-popup-close">Close</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', modalHtml);

  const popup = document.getElementById('notif-popup');
  const list  = document.getElementById('notif-popup-list');

  document.getElementById('notif-popup-close').addEventListener('click', () => {
    popup.style.display = 'none';
  });
  popup.addEventListener('click', (e) => {
    if (e.target === popup) popup.style.display = 'none';
  });

  list.addEventListener('click', async (e) => {
    const btn = e.target.closest('.notif-dismiss-btn');
    if (!btn) return;
    try {
      await fetch(`/api/notifications/${btn.dataset.id}/dismiss`, { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN } });
      btn.closest('.notif-item').remove();
      if (!list.querySelector('.notif-item')) popup.style.display = 'none';
    } catch {}
  });

  async function checkNotifications() {
    try {
      const res = await fetch('/api/notifications');
      if (!res.ok) return;
      const data = await res.json();
      if (!data.notifications?.length) return;
      list.innerHTML = data.notifications.map(n => `
        <div class="notif-item message message-warning" style="margin-bottom:0.75rem;display:flex;align-items:flex-start;gap:0.75rem;">
          <div style="flex:1">${escHtml(n.message)}</div>
          <button class="notif-dismiss-btn btn btn-sm btn-secondary" data-id="${n.id}" style="flex-shrink:0;">Dismiss</button>
        </div>`).join('');
      popup.style.display = 'flex';
    } catch {}
  }

  checkNotifications();
})();
