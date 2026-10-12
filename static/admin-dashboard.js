// Admin dashboard: dependency-free SVG charts, table filters, row expanders,
// and the edit / confirm modals. Chart data comes from <script id="dash-data">.
(function () {
  const SVG = 'http://www.w3.org/2000/svg';
  const data = JSON.parse(document.getElementById('dash-data')?.textContent || '{}');
  const tooltip = document.getElementById('dash-tooltip');

  function el(name, attrs = {}, parent) {
    const node = document.createElementNS(SVG, name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceMax(max) {
    if (max <= 4) return 4;
    const pow = 10 ** Math.floor(Math.log10(max));
    const step = [1, 2, 2.5, 5, 10].find((s) => s * pow * 4 >= max) * pow;
    return step * 4;
  }

  // Bar with 4px rounded data-end, anchored square to the baseline.
  function barPath(x, y, w, h, r = 4) {
    if (h <= 0) return '';
    const rr = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
  }

  function hBarPath(x, y, w, h, r = 4) {
    if (w <= 0) return '';
    const rr = Math.min(r, h / 2, w);
    return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
  }

  function showTip(evt, value, label) {
    tooltip.replaceChildren();
    const v = document.createElement('strong');
    v.textContent = value;
    const l = document.createElement('span');
    l.textContent = label;
    tooltip.append(v, l);
    tooltip.hidden = false;
    const r = evt.target.getBoundingClientRect ? evt.target.getBoundingClientRect() : null;
    const x = evt.clientX ?? (r ? r.left + r.width / 2 : 0);
    const y = evt.clientY ?? (r ? r.top : 0);
    const tw = tooltip.offsetWidth;
    tooltip.style.left = `${Math.min(window.innerWidth - tw - 8, Math.max(8, x - tw / 2))}px`;
    tooltip.style.top = `${Math.max(8, y - tooltip.offsetHeight - 12)}px`;
  }

  let hoverMark = null;

  function hideTip() {
    tooltip.hidden = true;
    hoverMark?.classList.remove('is-hover');
    hoverMark = null;
  }

  // The tooltip is position: fixed, so it would stay put while the page
  // scrolls out from under it. Hide it on any scroll or resize.
  window.addEventListener('scroll', hideTip, { capture: true, passive: true });
  window.addEventListener('resize', hideTip);
  document.addEventListener('pointerdown', (e) => { if (!e.target.closest?.('svg')) hideTip(); });

  function bindHover(target, value, label, mark) {
    target.setAttribute('tabindex', '0');
    target.setAttribute('aria-label', `${label}: ${value}`);
    const on = (e) => {
      if (hoverMark && hoverMark !== mark) hoverMark.classList.remove('is-hover');
      hoverMark = mark || null;
      mark?.classList.add('is-hover');
      showTip(e, value, label);
    };
    const off = () => hideTip();
    target.addEventListener('pointermove', on);
    target.addEventListener('pointerleave', off);
    // Keyboard focus only: a mouse click also focuses the bar, which would
    // otherwise leave the tooltip up after the pointer moves away.
    target.addEventListener('focus', () => {
      if (!target.matches(':focus-visible')) return;
      const r = target.getBoundingClientRect();
      on({ target, clientX: r.left + r.width / 2, clientY: r.top });
    });
    target.addEventListener('blur', off);
  }

  function tableView(rows, headers) {
    const details = document.createElement('details');
    details.className = 'dash-tableview';
    const summary = document.createElement('summary');
    summary.textContent = 'Show as table';
    const table = document.createElement('table');
    table.className = 'dash-table dash-table-compact';
    const thead = table.createTHead().insertRow();
    headers.forEach((h, i) => {
      const th = document.createElement('th');
      th.textContent = h;
      if (i > 0) th.className = 'num';
      thead.appendChild(th);
    });
    const tbody = table.createTBody();
    rows.forEach((r) => {
      const tr = tbody.insertRow();
      r.forEach((cell, i) => {
        const td = tr.insertCell();
        td.textContent = cell;
        if (i > 0) td.className = 'num';
      });
    });
    details.append(summary, table);
    return details;
  }

  // Vertical bars over days. One series, one color.
  function renderBars(plot, series, unit) {
    const width = Math.max(260, plot.clientWidth);
    const height = 200;
    const m = { top: 12, right: 8, bottom: 26, left: 32 };
    const iw = width - m.left - m.right;
    const ih = height - m.top - m.bottom;
    const max = niceMax(Math.max(0, ...series.map((d) => d.value)));
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });

    // Recessive hairline grid + y ticks.
    for (let i = 0; i <= 4; i += 1) {
      const v = (max / 4) * i;
      const y = m.top + ih - (v / max) * ih;
      el('line', { x1: m.left, x2: width - m.right, y1: y, y2: y, class: i === 0 ? 'dash-axis' : 'dash-grid' }, svg);
      const t = el('text', { x: m.left - 6, y: y + 4, class: 'dash-tick', 'text-anchor': 'end' }, svg);
      t.textContent = Number.isInteger(v) ? v : v.toFixed(1);
    }

    const step = iw / series.length;
    const gap = Math.min(2, step * 0.2);
    const bw = Math.max(1, step - gap);
    const labelEvery = Math.ceil(series.length / Math.max(2, Math.floor(iw / 44)));
    const maxIdx = series.reduce((best, d, i) => (d.value > series[best].value ? i : best), 0);

    series.forEach((d, i) => {
      const x = m.left + i * step + gap / 2;
      const h = (d.value / max) * ih;
      const mark = el('path', { d: barPath(x, m.top + ih - h, bw, h), class: 'dash-bar' }, svg);
      // Hit target = the whole column, not just the painted bar.
      const hit = el('rect', { x: m.left + i * step, y: m.top, width: step, height: ih, class: 'dash-hit' }, svg);
      bindHover(hit, `${d.value} ${unit}`, d.label, mark);
      // Count label slots back from the newest day so the last label never collides.
      if ((series.length - 1 - i) % labelEvery === 0) {
        const t = el('text', { x: x + bw / 2, y: height - 8, class: 'dash-tick', 'text-anchor': 'middle' }, svg);
        t.textContent = d.label;
      }
    });

    // Selective direct label: only the peak.
    if (series[maxIdx] && series[maxIdx].value > 0) {
      const d = series[maxIdx];
      const x = m.left + maxIdx * step + step / 2;
      const y = m.top + ih - (d.value / max) * ih - 5;
      const t = el('text', { x, y, class: 'dash-value', 'text-anchor': 'middle' }, svg);
      t.textContent = d.value;
    }

    const total = series.reduce((s, d) => s + d.value, 0);
    plot.replaceChildren(svg);
    const foot = document.createElement('p');
    foot.className = 'dash-chart-total';
    foot.textContent = `${total} total`;
    plot.append(foot, tableView(series.map((d) => [d.label, d.value]), ['Day', 'Count']));
  }

  // Horizontal ranked bars (categories), value labels at bar ends.
  function renderHBars(plot, series) {
    if (!series.length) {
      plot.innerHTML = '<p class="text-muted text-sm dash-empty">No carpools in this range yet.</p>';
      return;
    }
    const width = Math.max(260, plot.clientWidth);
    const row = 26;
    const m = { top: 4, right: 36, bottom: 4, left: 48 };
    const height = m.top + m.bottom + series.length * row;
    const iw = width - m.left - m.right;
    const max = Math.max(1, ...series.map((d) => d.value));
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });
    series.forEach((d, i) => {
      const y = m.top + i * row + 3;
      const h = row - 8;
      const w = (d.value / max) * iw;
      const label = el('text', { x: m.left - 8, y: y + h / 2 + 4, class: 'dash-cat', 'text-anchor': 'end' }, svg);
      label.textContent = d.label;
      const mark = el('path', { d: hBarPath(m.left, y, w, h), class: 'dash-bar' }, svg);
      const val = el('text', { x: m.left + w + 6, y: y + h / 2 + 4, class: 'dash-value' }, svg);
      val.textContent = d.value;
      const hit = el('rect', { x: 0, y: m.top + i * row, width, height: row, class: 'dash-hit' }, svg);
      bindHover(hit, `${d.value} carpool${d.value === 1 ? '' : 's'}`, d.label, mark);
    });
    plot.replaceChildren(svg, tableView(series.map((d) => [d.label, d.value]), ['Airport', 'Carpools']));
  }

  // Part-to-whole: one 100% stacked bar + legend with counts and percentages.
  function renderStack(plot, series) {
    const total = series.reduce((s, d) => s + d.value, 0);
    plot.replaceChildren();
    if (!total) {
      const empty = document.createElement('p');
      empty.className = 'text-muted text-sm dash-empty';
      empty.textContent = plot.closest('.dash-chart')?.dataset.key === 'outcomes'
        ? 'No carpools have finished in this range yet.'
        : 'No carpools in this range yet.';
      plot.replaceChildren(empty);
      return;
    }
    const width = Math.max(260, plot.clientWidth);
    const height = 28;
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img' });
    const shown = series.filter((d) => d.value > 0);
    const gaps = (shown.length - 1) * 2;
    let x = 0;
    shown.forEach((d, i) => {
      const w = ((width - gaps) * d.value) / total;
      const first = i === 0;
      const last = i === shown.length - 1;
      const r = 4;
      const path = `M${x + (first ? r : 0)},0H${x + w - (last ? r : 0)}${last ? `Q${x + w},0 ${x + w},${r}V${height - r}Q${x + w},${height} ${x + w - r},${height}` : `V${height}`}H${x + (first ? r : 0)}${first ? `Q${x},${height} ${x},${height - r}V${r}Q${x},0 ${x + r},0` : `V0`}Z`;
      const mark = el('path', { d: path, class: `dash-seg dash-seg-${d.key}` }, svg);
      bindHover(mark, `${d.value} (${Math.round((100 * d.value) / total)}%)`, d.label, mark);
      x += w + 2;
    });
    const legend = document.createElement('ul');
    legend.className = 'dash-legend';
    series.forEach((d) => {
      const li = document.createElement('li');
      const sw = document.createElement('span');
      sw.className = `dash-swatch dash-seg-${d.key}`;
      const name = document.createElement('span');
      name.textContent = d.label;
      const val = document.createElement('strong');
      val.textContent = `${d.value} · ${Math.round((100 * d.value) / total)}%`;
      li.append(sw, name, val);
      legend.appendChild(li);
    });
    plot.append(svg, legend);
  }

  const units = { created: 'carpools', signups: 'new students', joins: 'joins', active_people: 'active students', upcoming: 'carpools departing' };

  function renderAll() {
    document.querySelectorAll('.dash-chart').forEach((fig) => {
      const plot = fig.querySelector('.dash-plot');
      const series = data[fig.dataset.key] || [];
      const open = plot.querySelector('details')?.open;
      if (fig.dataset.chart === 'bars') renderBars(plot, series, units[fig.dataset.key] || '');
      if (fig.dataset.chart === 'hbars') renderHBars(plot, series);
      if (fig.dataset.chart === 'stack') renderStack(plot, series);
      if (open) plot.querySelector('details')?.setAttribute('open', '');
    });
  }

  renderAll();
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderAll, 150);
  });

  // Expandable carpool rows.
  document.querySelectorAll('.dash-row-toggle').forEach((row) => {
    const toggle = () => {
      const detail = document.getElementById(row.dataset.target);
      const open = detail.hidden;
      detail.hidden = !open;
      row.setAttribute('aria-expanded', String(open));
      row.classList.toggle('is-open', open);
    };
    row.addEventListener('click', toggle);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });

  // Search boxes filter their table's rows.
  document.querySelectorAll('.dash-filter').forEach((input) => {
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll(`#${input.dataset.filter} tbody tr`).forEach((tr) => {
        tr.hidden = q && !tr.textContent.toLowerCase().includes(q);
      });
    });
  });

  // Edit modal.
  const modal = document.getElementById('edit-modal');
  const editForm = document.getElementById('edit-form');
  document.querySelectorAll('.edit-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      editForm.action = `/admin/edit/${btn.dataset.id}`;
      ['first_name', 'last_initial', 'phone', 'flight_code', 'airport_code', 'seats_available', 'notes', 'planned_departure_time']
        .forEach((f) => { document.getElementById(`edit-${f}`).value = btn.dataset[f] || ''; });
      modal.style.display = 'flex';
    });
  });
  document.getElementById('edit-cancel').addEventListener('click', () => { modal.style.display = 'none'; });
  modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });

  // Confirm modal for destructive forms.
  const confirmModal = document.getElementById('confirm-modal');
  let pending = null;
  function closeConfirm(ok) {
    confirmModal.style.display = 'none';
    const action = pending;
    pending = null;
    if (ok && action) action();
  }
  document.querySelectorAll('.confirmable-form').forEach((form) => {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      pending = () => form.submit();
      document.getElementById('confirm-title').textContent = form.dataset.confirmTitle || 'Confirm action';
      document.getElementById('confirm-text').textContent = form.dataset.confirmMessage || 'Are you sure?';
      document.getElementById('confirm-confirm').textContent = form.dataset.confirmLabel || 'Continue';
      confirmModal.style.display = 'flex';
    });
  });
  document.getElementById('confirm-cancel').addEventListener('click', () => closeConfirm(false));
  document.getElementById('confirm-confirm').addEventListener('click', () => closeConfirm(true));
  confirmModal.addEventListener('click', (e) => { if (e.target === confirmModal) closeConfirm(false); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && confirmModal.style.display !== 'none') closeConfirm(false);
  });

  // Hamburger menu.
  const hbBtn = document.getElementById('hamburger-btn');
  const hbMenu = document.getElementById('hamburger-menu');
  hbBtn?.addEventListener('click', () => hbMenu.classList.toggle('open'));
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.hamburger-wrap')) hbMenu.classList.remove('open');
  });
})();
