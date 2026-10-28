const $ = (id) => document.getElementById(id);
let mode = 'signin'; // 'signin' or 'signup'

function setMsg(text, isError = false) {
  $('login-message').textContent = text;
  $('login-message').className = isError ? 'error' : '';
}

function render() {
  const signup = mode === 'signup';
  $('login-heading').textContent = signup ? 'Create account' : 'Sign in';
  $('login-sub').textContent = signup
    ? 'Choose a username and a password of 8 or more characters.'
    : 'Welcome back! Sign in to continue.';
  $('confirm-row').hidden = !signup;
  $('confirm').required = signup;
  $('password').autocomplete = signup ? 'new-password' : 'current-password';
  $('login-btn').textContent = signup ? 'Create account' : 'Sign in';
  $('switch-text').textContent = signup ? 'Already have an account?' : "Don't have an account?";
  $('switch-link').textContent = signup ? 'Sign in' : 'Create one';
  setMsg('');
}

$('switch-link').addEventListener('click', (e) => {
  e.preventDefault();
  mode = mode === 'signin' ? 'signup' : 'signin';
  $('password').value = '';
  $('confirm').value = '';
  render();
});

async function init() {
  try {
    const res = await fetch('/api/session');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $('login-sub').textContent = '';
      return setMsg(data.error || 'Could not reach the server', true);
    }
    if (data.logged_in) return location.replace('/');
    // Nobody has an account yet: start on the Create account form
    mode = data.needs_setup ? 'signup' : 'signin';
    render();
  } catch {
    $('login-sub').textContent = '';
    setMsg('Could not reach the server', true);
  }
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const signup = mode === 'signup';
  if (signup && $('password').value !== $('confirm').value) {
    return setMsg('The two passwords do not match', true);
  }
  $('login-btn').disabled = true;
  setMsg('');
  try {
    const res = await fetch(signup ? '/api/signup' : '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMsg(data.error || (signup ? 'Could not create the account' : 'Could not sign in'), true);
      $('password').value = '';
      $('confirm').value = '';
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