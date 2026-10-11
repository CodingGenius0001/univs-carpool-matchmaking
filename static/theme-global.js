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

    // Phone-sized screens: offer the toggle inside the hamburger menu instead
    // of the floating button (CSS decides which one is visible).
    const menu = document.getElementById('hamburger-menu');
    if (menu) {
      const menuToggle = document.createElement('button');
      menuToggle.type = 'button';
      menuToggle.className = 'menu-button menu-theme-toggle';
      const syncLabel = function () {
        menuToggle.textContent = document.documentElement.classList.contains('light-mode')
          ? 'Switch to dark mode'
          : 'Switch to light mode';
      };
      syncLabel();
      menuToggle.addEventListener('click', function () {
        toggle.click();
        syncLabel();
      });
      const divider = document.createElement('div');
      divider.className = 'menu-divider menu-theme-toggle';
      menu.append(divider, menuToggle);
      document.body.classList.add('has-menu-theme-toggle');
    }
  });
})();
