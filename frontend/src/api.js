const base = ''

async function req(path, opts = {}) {
  const r = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await r.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) {
    const msg = (data && (data.detail || data.message)) || r.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  dashboard: () => req('/api/dashboard'),
  tills: () => req('/api/tills'),
  lines: (day) => req('/api/lines' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  createLine: (body) => req('/api/lines', { method: 'POST', body: JSON.stringify(body) }),
  patchLine: (id, body) => req(`/api/lines/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteLine: (id) => req(`/api/lines/${id}`, { method: 'DELETE' }),
  closePreview: (day) => req('/api/close/preview' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  seal: (body) => req('/api/close/seal', { method: 'POST', body: JSON.stringify(body) }),
  reopen: (body) => req('/api/close/reopen', { method: 'POST', body: JSON.stringify(body) }),
  batches: () => req('/api/close/batches'),
  variances: () => req('/api/close/variances'),
  getDay: () => req('/api/settings/business-day'),
  setDay: (business_day) => req('/api/settings/business-day', { method: 'PUT', body: JSON.stringify({ business_day }) }),
}
