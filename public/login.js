const $ = (id) => document.getElementById(id);
let firstRun = false; // true when no account exists yet

function setMsg(text, isError = false) {
  $('login-message').textContent = text;
  $('login-message').className = isError ? 'error' : '';
}

function render() {
  $('confirm-row').hidden = !firstRun;
  $('confirm').required = firstRun;
  $('password').autocomplete = firstRun ? 'new-password' : 'current-password';
  $('login-btn').textContent = firstRun ? 'Create account' : 'Sign in';
  $('login-sub').textContent = firstRun
    ? 'Welcome! Create the first account to get started (password: 8+ characters).'
    : 'Sign in to continue.';
}

async function init() {
  try {
    const res = await fetch('/api/session');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $('login-sub').textContent = '';
      return setMsg(data.error || 'Could not reach the server', true);
    }
    if (data.logged_in) return location.replace('/');
    firstRun = !!data.needs_setup;
    render();
  } catch {
    $('login-sub').textContent = '';
    setMsg('Could not reach the server', true);
  }
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (firstRun && $('password').value !== $('confirm').value) {
    return setMsg('The two passwords do not match', true);
  }
  $('login-btn').disabled = true;
  setMsg('');
  try {
    const res = await fetch(firstRun ? '/api/signup' : '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMsg(data.error || 'Could not sign in', true);
      $('password').value = '';
      return;
    }
    location.replace('/');
  } catch {
    setMsg('Could not reach the server', true);
  } finally {
    $('login-btn').disabled = false;
  }
});

init();
