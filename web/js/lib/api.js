// Local API client. The per-session token arrives in the boot block and is
// sent as a header, so other pages in the browser cannot call the API.
let token = '';

export function setToken(value) { token = value || ''; }
export function getToken() { return token; }

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, body, { timeout = 45000, raw = false, keepalive = false } = {}) {
  const init = {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Forts-Token': token },
    cache: 'no-store',
    keepalive,
  };
  if (!keepalive) init.signal = AbortSignal.timeout(timeout);
  if (body instanceof Blob) {
    init.body = body;
    init.headers['Content-Type'] = 'application/octet-stream';
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, init);
  if (raw) return response;
  let value;
  try {
    value = await response.json();
  } catch {
    throw new ApiError('', response.status);
  }
  if (!response.ok) throw new ApiError(value?.error || '', response.status);
  return value;
}

export function mapImageUrl(name) {
  return '/api/map-image?name=' + encodeURIComponent(name || '') + '&token=' + encodeURIComponent(token);
}
