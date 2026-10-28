// Dark / light theme. Loaded in <head> on every page so there is no flash of the wrong theme.
(function () {
  var KEY = 'theme';

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function preferred() {
    var s = saved();
    if (s === 'dark' || s === 'light') return s;
    return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.textContent = theme === 'dark' ? '𖤓˚࿔ Light' : '⋆☾.⭒ Dark';
      btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
    }
  }

  apply(preferred());

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle');
    apply(document.documentElement.getAttribute('data-theme'));
    if (!btn) return;
    btn.addEventListener('click', function () {
      var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      apply(next);
    });
  });
})();
