// US date entry (MM/DD/YYYY) for <input type="date" data-us-date>.
//
// Native date inputs display dates in the browser's region format, so a
// student whose computer is set to e.g. the UK sees DD/MM/YYYY and can swap
// day and month. This replaces the visible field with a text box that always
// reads MM/DD/YYYY, plus a calendar button. The original <input type="date">
// stays in the form (visually hidden) and keeps the YYYY-MM-DD value, so
// existing code that reads `.value` keeps working.
(function () {
  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function isoToUs(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? `${m[2]}/${m[3]}/${m[1]}` : '';
  }

  function usToIso(text) {
    const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text || '');
    if (!m) return '';
    const month = Number(m[1]);
    const day = Number(m[2]);
    const year = Number(m[3]);
    const date = new Date(year, month - 1, day);
    // Rejects 02/30, 13/01, etc. (Date silently rolls those over).
    if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) {
      return '';
    }
    return `${year}-${pad(month)}-${pad(day)}`;
  }

  function maskDigits(raw) {
    const digits = raw.replace(/\D/g, '').slice(0, 8);
    let out = digits.slice(0, 2);
    if (digits.length > 2) out += `/${digits.slice(2, 4)}`;
    if (digits.length > 4) out += `/${digits.slice(4)}`;
    return out;
  }

  function enhance(native) {
    if (native.dataset.usDateReady) return;
    native.dataset.usDateReady = '1';

    const wrap = document.createElement('span');
    wrap.className = 'us-date';
    native.parentNode.insertBefore(wrap, native);

    const text = document.createElement('input');
    text.type = 'text';
    text.inputMode = 'numeric';
    text.autocomplete = 'off';
    text.placeholder = 'MM/DD/YYYY';
    text.maxLength = 10;
    text.className = 'us-date-text';
    text.setAttribute('aria-label', `${native.getAttribute('aria-label') || 'Date'} (MM/DD/YYYY)`);
    if (native.required) {
      // A required field the user can't see would block form submission.
      native.required = false;
      text.required = true;
    }

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'us-date-btn';
    button.setAttribute('aria-label', 'Open calendar');
    button.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M7 2a1 1 0 0 1 1 1v1h8V3a1 1 0 1 1 2 0v1h1a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h1V3a1 1 0 0 1 1-1Zm13 8H4v9a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-9Z"/></svg>';

    native.classList.add('us-date-native');
    native.tabIndex = -1;
    native.setAttribute('aria-hidden', 'true');
    wrap.append(text, button, native);

    function syncFromNative() {
      text.value = isoToUs(native.value);
      text.setCustomValidity('');
    }

    function syncFromText(fireEvents) {
      const iso = usToIso(text.value);
      const complete = text.value.length === 10;
      text.setCustomValidity(text.value && (!complete || !iso) ? 'Enter a real date as MM/DD/YYYY' : '');
      if (native.value === iso) return;
      native.value = iso;
      if (fireEvents && (iso || !text.value)) {
        native.dispatchEvent(new Event('input', { bubbles: true }));
        native.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }

    text.addEventListener('input', () => {
      text.value = maskDigits(text.value);
      syncFromText(true);
    });
    text.addEventListener('blur', () => {
      if (text.value && text.validationMessage) text.reportValidity();
    });

    button.addEventListener('click', () => {
      try {
        native.showPicker();
      } catch {
        // Older browsers without showPicker(): fall back to typing.
        text.focus();
      }
    });

    // Existing code calls focus() on the original input (e.g. after a
    // validation error); send that to the visible box.
    native.addEventListener('focus', () => text.focus());
    native.addEventListener('change', syncFromNative);
    native.addEventListener('input', syncFromNative);

    // Code that sets `.value` directly (prefill, reset) doesn't fire events,
    // so mirror the property onto the visible box.
    const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(native, 'value', {
      configurable: true,
      get() {
        return proto.get.call(this);
      },
      set(v) {
        proto.set.call(this, v);
        if (document.activeElement !== text) syncFromNative();
      },
    });
    native.form?.addEventListener('reset', () => setTimeout(syncFromNative, 0));

    syncFromNative();
    native.usDateText = text;
  }

  function init() {
    document.querySelectorAll('input[type="date"][data-us-date]').forEach(enhance);
  }

  window.usDate = { isoToUs, usToIso };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
