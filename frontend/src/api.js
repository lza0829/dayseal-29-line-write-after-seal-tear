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
  importLines: (body) => req('/api/lines/import', { method: 'POST', body: JSON.stringify(body) }),
  reverseLine: (id, body = {}) => req(`/api/lines/${id}/reverse`, { method: 'POST', body: JSON.stringify(body) }),
  moveLineDay: (id, business_day) => req(`/api/lines/${id}/day`, { method: 'PATCH', body: JSON.stringify({ business_day }) }),
  patchLine: (id, body) => req(`/api/lines/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteLine: (id) => req(`/api/lines/${id}`, { method: 'DELETE' }),
  pickups: (day) => req('/api/lines/pickups' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  createPickup: (body) => req('/api/lines/pickups', { method: 'POST', body: JSON.stringify(body) }),
  writePaths: (day) => req('/api/lines/write-paths' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  closePreview: (day) => req('/api/close/preview' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  seal: (body) => req('/api/close/seal', { method: 'POST', body: JSON.stringify(body) }),
  reopen: (body) => req('/api/close/reopen', { method: 'POST', body: JSON.stringify(body) }),
  batches: () => req('/api/close/batches'),
  variances: () => req('/api/close/variances'),
  conflicts: (day) => req('/api/close/conflicts' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  integrity: (day) => req('/api/close/integrity' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  repair: (body = {}) => req('/api/close/repair', { method: 'POST', body: JSON.stringify(body) }),
  getDay: () => req('/api/settings/business-day'),
  setDay: (business_day) => req('/api/settings/business-day', { method: 'PUT', body: JSON.stringify({ business_day }) }),
}
