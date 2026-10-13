<script>
import { api, setPassword, getPassword } from './lib/api.js'

let health = $state(null)
let config = $state(null)
let catalog = $state({ models: [] })
let suggestResult = $state(null)
let suggestBusy = $state(false)
let applyBusy = $state(false)
let applyResult = $state(null)
let rolloutResult = $state(null)
let error = $state(null)
let pwOpen = $state(false)
let pwInput = $state(getPassword())
let catFilterOpenGoOnly = $state(true)
let priceTableShowAll = $state(false)

// Formular-Zustand (Tier-Editor)
let fast = $state({ model: '', variant: '' })
let medium = $state({ model: '', variant: '' })
let heavy = $state({ model: '', variant: '' })

const TIER_INFO = {
  fast: { title: '@fast', role: 'Nur-lesende Exploration, Suche, Grep — viele kleine Dispatches' },
  medium: { title: '@medium', role: 'Implementation, Refactoring, Tests — Arbeitspferd' },
  heavy: { title: '@heavy', role: 'Architektur, Debugging (nach Fehlschlägen), Security-Review' },
}

const goModels = $derived(catalog.models.filter(m => m.providerID === 'opencode-go' && m.status === 'active'))
const tableModels = $derived(catFilterOpenGoOnly ? goModels : catalog.models.filter(m => m.status === 'active'))

async function load() {
  error = null
  try {
    ;[health, config, catalog] = await Promise.all([
      api('/api/health'),
      api('/api/config'),
      api('/api/catalog'),
    ])
    const t = config.tiers || {}
    for (const tier of ['fast', 'medium', 'heavy']) {
      const cur = t[tier] || {}
      const src = { fast, medium, heavy }[tier]
      src.model = cur.model || ''
      src.variant = cur.variant || ''
    }
  } catch (e) {
    error = e.message
  }
}

function modelOf(ref) {
  return catalog.models.find(m => m.ref === ref) || null
}

function variantOptions(ref) {
  const m = modelOf(ref)
  return ['(keine)', ...(m?.variants || [])]
}

function priceText(ref) {
  const m = modelOf(ref)
  if (!m || !m.cost) return ''
  const c = m.cost
  if (!c.input && !c.output) return 'free'
  return `$${c.input ?? '?'}/${c.output ?? '?'}`
}

function tierNorm(tier) {
  const src = { fast, medium, heavy }[tier]
  return { model: src.model, variant: src.variant === '(keine)' ? '' : src.variant }
}

async function runSuggest() {
  suggestBusy = true
  suggestResult = null
  error = null
  try {
    // POST erzwingen (ohne Body würde fetch sonst GET senden)
    const r = await api('/api/suggest', { body: {}, method: 'POST' })
    if (!r || !r.decision) throw new Error('Unerwartete Antwort vom Server (kein Vorschlag enthalten)')
    suggestResult = r.decision
  } catch (e) {
    error = 'KI-Vorschlag: ' + e.message
  } finally {
    suggestBusy = false
  }
}

function acceptSuggestion() {
  for (const tier of ['fast', 'medium', 'heavy']) {
    const d = suggestResult?.[tier]
    if (!d) continue
    const src = { fast, medium, heavy }[tier]
    src.model = d.model
    src.variant = d.variant || '(keine)'
  }
}

async function runApply() {
  applyBusy = true
  applyResult = null
  error = null
  try {
    const body = {
      tiers: { fast: tierNorm('fast'), medium: tierNorm('medium'), heavy: tierNorm('heavy') },
    }
    applyResult = await api('/api/apply', { body })
    await load()
  } catch (e) {
    error = 'Anwenden: ' + e.message
  } finally {
    applyBusy = false
  }
}

async function runRollout() {
  rolloutResult = null
  try {
    rolloutResult = await api('/api/rollout', { body: {} })
  } catch (e) {
    error = 'Rollout: ' + e.message
  }
}

load()
</script>

<header>
  <div class="brand">
    <img src="/hestia.svg" alt="Hestia" width="34" height="34" />
    <div>
      <h1>Hestia</h1>
      <span class="muted">OpenCode Model Panel · Fast / Medium / Heavy · CI/CD-Rollout</span>
    </div>
  </div>
  <div class="status">
    {#if health}
      <span class="badge {health.status === 'ok' ? 'active' : 'err'}">{health.status}</span>
    {/if}
    <button onclick={() => (pwOpen = !pwOpen)}>🔐</button>
  </div>
</header>

{#if pwOpen}
  <div class="card">
    <h2>Authentifizierung</h2>
    <form onsubmit={(e) => { e.preventDefault(); setPassword(pwInput); pwOpen = false; load() }}>
      <input type="password" bind:value={pwInput} placeholder="Hestia-Passwort" style="width:260px" />
      <button type="submit">Speichern</button>
    </form>
    <p class="muted">Passwort liegt nur in deinem Browser (sessionStorage).</p>
  </div>
{/if}

{#if error}<div class="card errbox">⚠️ {error}</div>{/if}

<div class="card">
  <h2>Aktive Tier-Konfiguration</h2>
  {#if config}
    <table>
      <thead><tr><th>Tier</th><th>Modell</th><th>Variante</th><th>Preis in/out $/M</th></tr></thead>
      <tbody>
        {#each ['fast', 'medium', 'heavy'] as tier}
          <tr>
            <td>{TIER_INFO[tier].title}</td>
            <td class="mono">{config.tiers?.[tier]?.model || '—'}</td>
            <td class="muted">{config.tiers?.[tier]?.variant || '—'}</td>
            <td>{priceText(config.tiers?.[tier]?.model || '')}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if config.html_url}
      <p class="muted">
        Letzter Commit:
        <a href={config.html_url} target="_blank" rel="noreferrer">{config.sha?.slice(0, 8)}</a>
        · Preset: {config.activePreset} · Rollout via CI/CD (validate → Coder-Restart)
      </p>
    {/if}
  {:else}<p class="muted">Lade Konfiguration…</p>{/if}
</div>

<div class="card">
  <h2>KI-Vorschlag</h2>
  <p class="muted">
    OpenCode analysiert den Live-Katalog (alle OpenCode-Go-Modelle mit Preisen) und schlägt die
    günstigste zuverlässige 3-Tier-Anordnung vor. Dauer: 1–5 Minuten.
  </p>
  <button class="primary" onclick={runSuggest} disabled={suggestBusy}>
    {suggestBusy ? 'Analysiere (Katalog → KI → Vorschlag)…' : '🔀 KI-Vorschlag erstellen'}
  </button>
  {#if suggestResult}
    <div class="suggest">
      <p class="muted">{suggestResult.reasoning}</p>
      <table>
        <thead><tr><th>Tier</th><th>Vorschlag</th><th>Variante</th><th>Begründung</th></tr></thead>
        <tbody>
          {#each ['fast', 'medium', 'heavy'] as tier}
            <tr>
              <td>{TIER_INFO[tier].title}</td>
              <td class="mono">{suggestResult[tier]?.model}</td>
              <td class="muted">{suggestResult[tier]?.variant || '—'}</td>
              <td class="muted">{suggestResult[tier]?.reason}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      <div class="row">
        <button onclick={acceptSuggestion}>↓ In den Editor übernehmen</button>
      </div>
    </div>
  {/if}
</div>

<div class="card">
  <h2>Tier-Editor</h2>
  {#each ['fast', 'medium', 'heavy'] as tier}
    {@const src = { fast, medium, heavy }[tier]}
    <div class="tierRow">
      <div class="tierLabel">
        <strong>{TIER_INFO[tier].title}</strong>
        <span class="muted">{TIER_INFO[tier].role}</span>
      </div>
      <select bind:value={src.model} onchange={() => (src.variant = src.variant || '(keine)')}>
        <option value="" disabled>Modell wählen…</option>
        {#each goModels as m}
          <option value={m.ref}>{m.name} · {m.ref.replace('opencode-go/', '')} · {priceText(m.ref)}</option>
        {/each}
      </select>
      <select bind:value={src.variant}>
        {#each variantOptions(src.model) as v}<option value={v}>{v}</option>{/each}
      </select>
    </div>
  {/each}
  <div class="row">
    <button class="primary" onclick={runApply} disabled={applyBusy}>
      {applyBusy ? 'Validiere & committe…' : '✔ Anwenden (Commit + CI/CD-Rollout)'}
    </button>
    <button onclick={runRollout}>⏩ Force-Rollout</button>
    {#if applyResult?.commit}
      <span class="muted">
        Commit <a href={applyResult.commit.html_url} target="_blank" rel="noreferrer">{applyResult.commit.sha?.slice(0, 8)}</a>
        — {applyResult.rollout}
      </span>
    {/if}
    {#if rolloutResult?.dispatched}<span class="muted">Rollout-Workflow gestartet.</span>{/if}
  </div>
</div>

<div class="card">
  <h2>
    Preisübersicht
    <label class="muted" style="font-weight:400; margin-left:12px">
      <input type="checkbox" bind:checked={catFilterOpenGoOnly} /> nur OpenCode-Go
    </label>
  </h2>
  <table>
    <thead>
      <tr><th>Modell</th><th>ID</th><th>Status</th><th>In $/M</th><th>Out $/M</th><th>Cache r/w</th><th>Kontext</th><th>Variants</th><th>Release</th></tr>
    </thead>
    <tbody>
      {#each tableModels.slice(0, 40) as m}
        <tr>
          <td>{m.name}</td>
          <td class="mono">{m.ref.replace(PRESET_REF_TRIM, '')}</td>
          <td><span class="badge {m.status === 'active' ? 'active' : 'warn'}">{m.status}</span></td>
          <td>{m.cost.input ?? 0}</td>
          <td>{m.cost.output ?? 0}</td>
          <td>{m.cost.cacheRead ?? 0} / {m.cost.cacheWrite ?? 0}</td>
          <td>{((m.limit?.context || 0) / 1000).toFixed(0)}k</td>
          <td class="muted">{(m.variants || []).join(', ') || '—'}</td>
          <td class="muted">{m.released ? new Date(m.released * 1000).toISOString().slice(0, 10) : '—'}</td>
        </tr>
      {/each}
    </tbody>
  </table>
  <p class="muted">{tableModels.length} Modelle im Katalog (Live-Quelle: OpenCode-Server /api/model)</p>
</div>

<style>
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; }
  .brand { display: flex; gap: 12px; align-items: center; }
  .status { display: flex; gap: 8px; align-items: center; }
  .tierRow { display: grid; grid-template-columns: 1fr 2fr 1fr; gap: 10px; align-items: center; margin-bottom: 10px; }
  .tierLabel { display: flex; flex-direction: column; gap: 2px; }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
  .suggest { margin-top: 14px; }
  .errbox { border-color: var(--err); }
</style>
