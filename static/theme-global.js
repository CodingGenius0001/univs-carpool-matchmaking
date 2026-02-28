(function () {
  function applyTheme(theme) {
    const isLight = theme === 'light';
    document.documentElement.classList.toggle('light-mode', isLight);
  }

  function savedTheme() {
    return localStorage.getItem('theme') || 'dark';
  }

  applyTheme(savedTheme());

  document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('global-theme-toggle')) return;

    const toggle = document.createElement('button');
    toggle.id = 'global-theme-toggle';
    toggle.className = 'global-theme-toggle';
    toggle.setAttribute('aria-label', 'Toggle light or dark mode');
    toggle.innerHTML = '<span class="sun-icon">☀</span><span class="moon-icon">☾</span>';

    toggle.addEventListener('click', function () {
      const next = document.documentElement.classList.contains('light-mode') ? 'dark' : 'light';
      localStorage.setItem('theme', next);
      applyTheme(next);
    });

    document.body.appendChild(toggle);
  });
})();
