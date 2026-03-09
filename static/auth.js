// auth.js — JWT storage, login/register/logout helpers

const AUTH_KEY = 'lenti_auth';

function _store(token, refreshToken, user) {
  localStorage.setItem(AUTH_KEY, JSON.stringify({ token, refreshToken, user }));
}

function _load() {
  try { return JSON.parse(localStorage.getItem(AUTH_KEY)) || null; }
  catch { return null; }
}

export function getUser() {
  return _load()?.user || null;
}

export function authHeader() {
  const token = _load()?.token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function isLoggedIn() {
  return !!_load()?.token;
}

export function logout() {
  localStorage.removeItem(AUTH_KEY);
  window.location.href = '/login';
}

export async function login(email, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Login failed');
  }
  const data = await res.json();
  _store(data.access_token, data.refresh_token, data.user);
  return data.user;
}

export async function register(email, displayName, password) {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, display_name: displayName, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Registration failed');
  }
  const data = await res.json();
  _store(data.access_token, data.refresh_token, data.user);
  return data.user;
}

export async function refreshTokens() {
  const stored = _load();
  if (!stored?.refreshToken) return false;
  const res = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: stored.refreshToken }),
  });
  if (!res.ok) { localStorage.removeItem(AUTH_KEY); return false; }
  const data = await res.json();
  _store(data.access_token, data.refresh_token, data.user);
  return true;
}

// Call this on every page to inject the auth nav items.
// Pass `activePage` as 'map', 'stations', or null for auth pages.
export function renderNavAuth(activePage) {
  const user = getUser();
  const navUser = document.getElementById('navUser');
  if (!navUser) return;

  if (user) {
    navUser.innerHTML = `
      <span class="nav-user-name">${escHtml(user.display_name)}</span>
      <button class="nav-btn" id="logoutBtn">Sign out</button>`;
    document.getElementById('logoutBtn').addEventListener('click', logout);
  } else {
    navUser.innerHTML = `<a href="/login" class="nav-link${activePage === 'login' ? ' active' : ''}">Sign in</a>`;
  }
}

function escHtml(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
