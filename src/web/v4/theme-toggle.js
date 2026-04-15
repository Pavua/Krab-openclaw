(function() {
  // Init: apply stored preference before DOM ready to avoid flash
  const stored = localStorage.getItem('krab_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', stored);

  // Toggle function (global)
  window.toggleTheme = function() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('krab_theme', next);
    updateThemeIcon();
    return next;
  };

  function updateThemeIcon() {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    // Show sun icon when dark (click -> go light); moon when light (click -> go dark)
    btn.textContent = isDark ? '\u2600' : '\u263E';
    btn.title = isDark ? 'Switch to light theme' : 'Switch to dark theme';
    btn.setAttribute('aria-label', btn.title);
  }

  // On DOMContentLoaded - inject toggle button in navbar if not exists
  document.addEventListener('DOMContentLoaded', function() {
    const nav = document.querySelector('.nav-bell-wrapper');
    if (!nav) return;
    if (document.getElementById('theme-toggle-btn')) {
      updateThemeIcon();
      return;
    }
    const btn = document.createElement('button');
    btn.id = 'theme-toggle-btn';
    btn.className = 'theme-toggle';
    btn.type = 'button';
    btn.onclick = window.toggleTheme;
    btn.style.cssText = 'background:transparent; border:none; cursor:pointer; padding:0.5rem; color:inherit; font-size:1rem; margin-right:0.3rem; opacity:0.7; transition:opacity 0.15s;';
    btn.onmouseenter = function() { btn.style.opacity = '1'; };
    btn.onmouseleave = function() { btn.style.opacity = '0.7'; };
    nav.parentNode.insertBefore(btn, nav);
    updateThemeIcon();
  });
})();
