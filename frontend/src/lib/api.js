/**
 * API client.
 *
 * Money arrives as strings and stays strings. Nothing here parses an amount
 * into a JavaScript Number — `parseFloat("1053620.38")` is fine until it isn't,
 * and a payment system is exactly where it isn't (PRD 18).
 */

const BASE = '/api/v1';
const TOKEN_KEY = 'ipp.token';
const USER_KEY = 'ipp.user';

export const session = {
  get token() { return localStorage.getItem(TOKEN_KEY); },
  get user() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  },
  set({ token, user }) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
  can(permission) {
    return (this.user?.permissions || []).includes(permission);
  },
};

export class ApiError extends Error {
  constructor(status, errors) {
    const first = errors?.[0];
    super(first?.detail || `Request failed (${status})`);
    this.status = status;
    this.code = first?.code;
    this.errors = errors || [];
    this.extra = first || {};
  }
}

async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = {};
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401 && session.token) {
    session.clear();
    window.location.hash = '#/login';
  }

  if (raw) return response;

  const payload = await response.json().catch(() => ({ errors: [] }));
  if (!response.ok) throw new ApiError(response.status, payload.errors);
  return payload;
}

export const api = {
  // --- auth
  personas: () => request('/auth/personas'),
  login: (username) => request('/auth/login', { method: 'POST', body: { username } }),
  me: () => request('/auth/me'),
  health: () => request('/health'),

  // --- ingestion
  upload(file, source = 'MANUAL_UPLOAD') {
    const form = new FormData();
    form.append('file', file);
    form.append('source', source);
    return request('/invoices', { method: 'POST', body: form });
  },
  folderScan: () => request('/ingest/folder-scan', { method: 'POST' }),

  // --- retrieval
  invoices: (params = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== '' && v !== null),
    ).toString();
    return request(`/invoices${query ? `?${query}` : ''}`);
  },
  invoice: (id) => request(`/invoices/${id}`),
  extraction: (id) => request(`/invoices/${id}/extraction`),
  runs: (id) => request(`/invoices/${id}/runs`),
  runRules: (id, runId) => request(`/invoices/${id}/runs/${runId}/rules`),
  duplicates: (id) => request(`/invoices/${id}/duplicates`),
  documentUrl: (id) => `${BASE}/invoices/${id}/document?token=${encodeURIComponent(session.token || '')}`,
  ledger: (poNumber) => request(`/pos/${encodeURIComponent(poNumber)}/ledger`),
  dashboard: () => request('/dashboard/summary'),
  procurement: () => request('/masters/procurement'),
  match: (id) => request(`/invoices/${id}/match`),
  sheetUrl: (sheet) => `${BASE}/masters/download/${sheet}?token=${encodeURIComponent(session.token || '')}`,
  rules: () => request('/rules'),
  vendors: () => request('/vendors'),
  pos: () => request('/pos'),
  reasonCodes: () => request('/reason-codes'),
  fixtures: () => request('/fixtures'),
  audit: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/audit${query ? `?${query}` : ''}`);
  },
  verifyAudit: () => request('/audit/verify'),

  // --- actions
  correctField: (id, path, value, reason = 'OCR_CORRECTION') =>
    request(`/invoices/${id}/fields`, { method: 'PATCH', body: { path, value, reason } }),
  confirm: (id) => request(`/invoices/${id}/decision`, { method: 'POST', body: { action: 'CONFIRM' } }),
  overrideRequirements: (id, humanDecision, reasonCode) =>
    request(`/invoices/${id}/override/requirements`, {
      method: 'POST', body: { humanDecision, reasonCode },
    }),
  override: (id, body) => request(`/invoices/${id}/override`, { method: 'POST', body }),
  releaseDuplicate: (id, body) => request(`/invoices/${id}/duplicate-release`, { method: 'POST', body }),
  confirmDuplicate: (id, body) => request(`/invoices/${id}/duplicate-confirm`, { method: 'POST', body }),
  requestInfo: (id, body) => request(`/invoices/${id}/request-info`, { method: 'POST', body }),
  replay: (id) => request(`/invoices/${id}/replay`, { method: 'POST' }),
  reset: () => request('/admin/reset', { method: 'POST' }),
};

/**
 * Subscribe to the processing stream.
 *
 * EventSource cannot send an Authorization header, so the token rides as a
 * query parameter on this one route. Returns a close function.
 */
export function streamInvoice(id, handlers = {}, trigger = 'INITIAL') {
  const url = `${BASE}/invoices/${id}/stream?trigger=${trigger}&token=${encodeURIComponent(session.token || '')}`;
  const source = new EventSource(url);

  const bind = (name) => {
    source.addEventListener(name, (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      handlers[name]?.(payload);
    });
  };

  ['stage', 'field', 'rule', 'decision', 'explanation', 'security', 'done', 'error']
    .forEach(bind);

  source.addEventListener('done', () => source.close());
  source.addEventListener('error', () => {
    // A closed connection after `done` is the normal path, not a failure.
    if (source.readyState === EventSource.CLOSED) return;
    handlers.connectionLost?.();
  });

  return () => source.close();
}
