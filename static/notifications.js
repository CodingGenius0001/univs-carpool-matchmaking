// Notification bell + popup for unread notifications.
(function () {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const headerActions = document.querySelector('.hamburger-wrap');
  if (!headerActions) return;

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatNotificationTimestamp(isoValue) {
    if (!isoValue) return '';
    const parsed = new Date(isoValue);
    if (Number.isNaN(parsed.getTime())) return '';

    const parts = new Intl.DateTimeFormat(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
      timeZoneName: 'short',
    }).formatToParts(parsed);

    const time = parts
      .filter((part) => ['hour', 'minute', 'dayPeriod', 'literal'].includes(part.type))
      .map((part) => part.value)
      .join('')
      .replace(/\s+/g, ' ')
      .trim();
    const tz = parts.find((part) => part.type === 'timeZoneName')?.value?.trim() || '';

    if (!time && !tz) return '';
    if (!tz) return time;
    if (!time) return tz;
    return `${time} ${tz}`;
  }

  function decorateNotificationMessage(notification) {
    const message = String(notification?.message || '');
    const isJoinLeave = / joined your carpool(?: for .+?)?\.?$| left your carpool(?: for .+?)?\.?$/i.test(message);
    if (!isJoinLeave) {
      return message;
    }

    const timestamp = formatNotificationTimestamp(notification?.created_at);
    if (!timestamp) {
      return message;
    }

    return `${message.replace(/\.*$/, '')} at ${timestamp}.`;
  }

  const bellHtml = `
    <button type="button" class="notif-bell-btn" id="notif-bell-btn" aria-label="View notifications" aria-haspopup="dialog" aria-expanded="false">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3a4 4 0 0 0-4 4v1.06c0 .7-.2 1.39-.57 1.98L6.1 12.2A3 3 0 0 0 8.65 17h6.7a3 3 0 0 0 2.55-4.8l-1.33-2.16A3.78 3.78 0 0 1 16 8.06V7a4 4 0 0 0-4-4Zm0 18a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 21Z" fill="currentColor"/>
      </svg>
      <span class="notif-bell-dot" id="notif-bell-dot" hidden></span>
    </button>`;
  headerActions.insertAdjacentHTML('afterbegin', bellHtml);

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

  const bellBtn = document.getElementById('notif-bell-btn');
  const bellDot = document.getElementById('notif-bell-dot');
  const popup = document.getElementById('notif-popup');
  const list = document.getElementById('notif-popup-list');
  let unreadNotifications = [];

  function renderList() {
    if (!unreadNotifications.length) {
      list.innerHTML = `
        <div class="message" style="margin-bottom:0;">
          <div style="flex:1">You're all caught up.</div>
        </div>`;
      return;
    }

    list.innerHTML = unreadNotifications.map((n) => `
      <div class="notif-item message message-warning" style="margin-bottom:0.75rem;display:flex;align-items:flex-start;gap:0.75rem;">
        <div style="flex:1">${escHtml(decorateNotificationMessage(n))}</div>
        <button class="notif-dismiss-btn btn btn-sm btn-secondary" data-id="${n.id}" style="flex-shrink:0;">Dismiss</button>
      </div>`).join('');
  }

  function updateBellState() {
    const hasUnread = unreadNotifications.length > 0;
    bellDot.hidden = !hasUnread;
    bellBtn.setAttribute('aria-label', hasUnread ? `View notifications (${unreadNotifications.length} unread)` : 'View notifications');
  }

  function openPopup() {
    renderList();
    updateBellState();
    popup.style.display = 'flex';
    bellBtn.setAttribute('aria-expanded', 'true');
  }

  function closePopup() {
    popup.style.display = 'none';
    bellBtn.setAttribute('aria-expanded', 'false');
  }

  document.getElementById('notif-popup-close').addEventListener('click', () => {
    closePopup();
  });

  popup.addEventListener('click', (e) => {
    if (e.target === popup) closePopup();
  });

  bellBtn.addEventListener('click', () => {
    if (popup.style.display === 'flex') {
      closePopup();
      return;
    }
    openPopup();
  });

  list.addEventListener('click', async (e) => {
    const btn = e.target.closest('.notif-dismiss-btn');
    if (!btn) return;
    try {
      await fetch(`/api/notifications/${btn.dataset.id}/dismiss`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrfToken },
      });
      unreadNotifications = unreadNotifications.filter((notification) => String(notification.id) !== String(btn.dataset.id));
      renderList();
      updateBellState();
    } catch {}
  });

  async function checkNotifications() {
    try {
      const res = await fetch('/api/notifications');
      if (!res.ok) return;
      const data = await res.json();
      unreadNotifications = Array.isArray(data.notifications) ? data.notifications : [];
      updateBellState();
    } catch {}
  }

  checkNotifications();
})();
