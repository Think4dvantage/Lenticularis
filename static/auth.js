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

/**
 * Drop-in replacement for fetch() that automatically refreshes the access
 * token on a 401 and retries once. Redirects to /login if the refresh also
 * fails (session truly expired).
 */
export async function fetchAuth(url, options = {}) {
  const headers = { ...authHeader(), ...(options.headers || {}) };
  const res = await fetch(url, { ...options, headers });
  if (res.status !== 401) return res;

  // Try to refresh
  const ok = await refreshTokens();
  if (!ok) {
    window.location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    return res;
  }

  // Retry with fresh token
  const retryHeaders = { ...authHeader(), ...(options.headers || {}) };
  return fetch(url, { ...options, headers: retryHeaders });
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
      <button class="nav-btn" id="logoutBtn">${window.t('nav.sign_out')}</button>`;
    document.getElementById('logoutBtn').addEventListener('click', logout);

    // Inject Admin link for admin users
    if (user.role === 'admin') {
      const navLinks = document.querySelector('.nav-links');
      if (navLinks && !navLinks.querySelector('a[href="/admin"]')) {
        const link = document.createElement('a');
        link.href = '/admin';
        link.className = 'nav-link' + (activePage === 'admin' ? ' active' : '');
        link.textContent = window.t('nav.admin');
        navLinks.appendChild(link);
      }
    }
  } else {
    navUser.innerHTML = `<a href="/login" class="nav-link${activePage === 'login' ? ' active' : ''}">${window.t('nav.sign_in')}</a>`;
  }

  // Inject hamburger toggle for mobile nav (idempotent)
  const topNav = document.querySelector('.top-nav');
  if (topNav && !topNav.querySelector('.nav-hamburger')) {
    const btn = document.createElement('button');
    btn.className = 'nav-hamburger';
    btn.setAttribute('aria-label', 'Toggle navigation');
    btn.innerHTML = '&#9776;';
    btn.addEventListener('click', () => {
      topNav.querySelector('.nav-links')?.classList.toggle('open');
    });
    topNav.appendChild(btn);
  }
}

function escHtml(str) {
  return String(str).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
