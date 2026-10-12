// api.js — Hestia-Basic-Auth-Client. Passwort wird sessionStorage-seißig im Browser vermerkt.
const BASE = ''

export function getPassword() {
  return sessionStorage.getItem('hestia-pw') || ''
}

export function setPassword(pw) {
  sessionStorage.setItem('hestia-pw', pw)
}

function headers(extra = {}) {
  const pw = getPassword()
  const auth = 'Basic ' + btoa(`hestia:${pw}`)
  return { Authorization: auth, 'Content-Type': 'application/json', ...extra }
}

export async function api(path, opts = {}) {
  const method = (opts.method || (opts.body ? 'POST' : 'GET')).toUpperCase()
  const res = await fetch(BASE + path, {
    method,
    headers: headers(opts.headers || {}),
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  })
  let data = null
  try { data = await res.json() } catch { /* non-json */ }
  if (res.status === 401) throw new Error('401 — Hestia-Passwort falsch (hoch rechts eintragen)')
  if (!res.ok) throw new Error(data?.error || `${res.status} ${res.statusText}`)
  return data
}
