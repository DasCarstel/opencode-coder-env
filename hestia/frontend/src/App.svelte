<script>
import { api, setPassword, getPassword } from './lib/api.js'

let health = $state(null)
let config = $state(null)
let catalog = $state({ models: [] })
let suggestResult = $state(null)
let analyzeInfo = $state(null)
let suggestElapsed = $state(0)
let liveStatus = $state(null)
let analysisModel = $state('')
let analysisVariant = $state('(keine)')
let buildSel = $state({ model: '', variant: '(keine)' })
let planSel = $state({ model: '', variant: '(keine)' })
let primary = $state(null)

const VARIANT_RANK = { minimal: 1, none: 2, low: 3, medium: 4, thinking: 4, high: 5, xhigh: 6, max: 7 }
function highestVariantOf(ref) {
  const m = modelOf(ref)
  const vs = (m?.variants || []).filter(v => v in VARIANT_RANK)
  return vs.sort((a, b) => VARIANT_RANK[a] - VARIANT_RANK[b]).pop() || ''
}
function refOf(sel, fallback) {
  if (!sel.model) return fallback
  const v = sel.variant && sel.variant !== '(keine)' ? sel.variant : ''
  return sel.model + (v ? '#' + v : '')
}
const derivedBuild = $derived(medium.model ? medium.model + (medium.variant && medium.variant !== '(keine)' ? '#' + medium.variant : '') : '')
const derivedPlan = $derived(medium.model ? medium.model + (highestVariantOf(medium.model) ? '#' + highestVariantOf(medium.model) : '') : '')
const effBuild = $derived(refOf(buildSel, derivedBuild))
const effPlan = $derived(refOf(planSel, derivedPlan))
function onAnalysisModelChange() {
  const [m, v] = (analysisModel || '').split('#')
  analysisModel = m
  analysisVariant = v || '(keine)'
}
let suggestBusy = $state(false)
let applyBusy = $state(false)
let applyResult = $state(null)
let rolloutResult = $state(null)
let rolloutStatus = $state(null)
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

const allowed = $derived(catalog.providers || ['opencode-go'])
const goModels = $derived(catalog.models.filter(m => allowed.includes(m.providerID) && m.status === 'active'))
const tableModels = $derived(catFilterOpenGoOnly ? goModels : catalog.models.filter(m => m.status === 'active'))
function isFree(ref) { const m = modelOf(ref); return !!m && !m.cost.input && !m.cost.output }

async function load() {
  error = null
  try {
    ;[health, config, catalog] = await Promise.all([
      api('/api/health'),
      api('/api/config'),
      api('/api/catalog'),
    ])
    primary = config.primaryAgents || null
    for (const [key, sel] of [['build', buildSel], ['plan', planSel]]) {
      const p = primary?.[key]
      if (p?.manual) {
        sel.model = p.model || ''
        sel.variant = p.variant || '(keine)'
      } else {
        sel.model = ''
        sel.variant = '(keine)'
      }
    }
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

const committedHeavy = $derived(config?.tiers?.heavy?.model || '')
const committedHeavyLabel = $derived(
  config?.tiers?.heavy?.model
    ? config.tiers.heavy.model + (config.tiers.heavy.variant ? '#' + config.tiers.heavy.variant : '')
    : '—'
)
const editorHeavyLabel = $derived(
  heavy.model ? heavy.model + (heavy.variant && heavy.variant !== '(keine)' ? '#' + heavy.variant : '') : ''
)
const editorHeavyDiffers = $derived(
  !!heavy.model && (heavy.model !== committedHeavy || (heavy.variant !== '(keine)' && heavy.variant !== '' && heavy.variant !== (config?.tiers?.heavy?.variant || '')))
)

async function runSuggest() {
  suggestBusy = true
  suggestResult = null
  analyzeInfo = null
  error = null
  suggestElapsed = 0
  liveStatus = null
  const timer = setInterval(() => { suggestElapsed += 1 }, 1000)
  const poll = setInterval(async () => {
    try { liveStatus = await api('/api/suggest/status') } catch { /* ignore */ }
  }, 4000)
  try {
    const body = {}
    if (analysisModel) {
      body.model = analysisModel
      if (analysisVariant && analysisVariant !== '(keine)') body.variant = analysisVariant
    }
    const r = await api('/api/suggest', { body, method: 'POST' })
    if (!r || !r.decision) throw new Error('Unerwartete Antwort vom Server (kein Vorschlag enthalten)')
    suggestResult = r.decision
    analyzeInfo = r.analyzedWith || null
  } catch (e) {
    error = 'KI-Vorschlag: ' + e.message
  } finally {
    clearInterval(timer)
    clearInterval(poll)
    suggestBusy = false
    liveStatus = null
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
      primaryAgents: {
        build: { model: buildSel.model, variant: buildSel.variant === '(keine)' ? '' : buildSel.variant },
        plan: { model: planSel.model, variant: planSel.variant === '(keine)' ? '' : planSel.variant },
      },
    }
    applyResult = await api('/api/apply', { body })
    await load()
    await loadRolloutStatus()
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
    setTimeout(loadRolloutStatus, 8000)
  } catch (e) {
    error = 'Rollout: ' + e.message
  }
}

async function loadRolloutStatus() {
  try { rolloutStatus = await api('/api/rollout/status') } catch { /* ignore */ }
}

load()
loadRolloutStatus()
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
    günstigste zuverlässige 3-Tier-Anordnung vor. Das Modell darf ruhig länger „denken“ — solange
    Aktivität ankommt, läuft die Analyse weiter; erst bei kompletter Stille (~60 s) übernimmt der Fallback.
  </p>
  <div class="tierRow" style="grid-template-columns: 1fr 2fr 1fr">
    <div class="tierLabel">
      <strong>Analyse-Modell</strong>
      <span class="muted">Womit soll die Analyse rechnen?</span>
    </div>
    <select bind:value={analysisModel} onchange={onAnalysisModelChange}>
      <option value="">Automatisch — committetes @heavy ({committedHeavyLabel})</option>
      {#if effBuild}<option value={effBuild}>Build ({buildSel.model ? 'manuell' : 'hergeleitet von @medium'}) · {effBuild}</option>{/if}
      {#if effPlan}<option value={effPlan}>Plan ({planSel.model ? 'manuell' : 'hergeleitet von @medium'}) · {effPlan}</option>{/if}
      {#if editorHeavyDiffers}
        <option value={heavy.model}>Editor-Auswahl ({editorHeavyLabel})</option>
      {/if}
      {#each goModels as m}
        <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}</option>
      {/each}
    </select>
    <select bind:value={analysisVariant} disabled={!analysisModel}>
      {#each variantOptions(analysisModel || committedHeavy) as v}<option value={v}>{v}</option>{/each}
    </select>
  </div>
  <button class="primary" onclick={runSuggest} disabled={suggestBusy}>
    {suggestBusy ? `Analysiere … ${suggestElapsed}s` : '🔀 KI-Vorschlag erstellen'}
  </button>
  {#if suggestBusy}
    <p class="muted">
      {#if liveStatus?.model}
        Analysiert gerade mit <span class="mono">{liveStatus.model}</span>
        {#if liveStatus.attempt && liveStatus.total}· Versuch {liveStatus.attempt}/{liveStatus.total}{/if}
        {#if liveStatus.deltas}· {liveStatus.deltas} Denk-Schritte{/if}
        · {suggestElapsed}s
      {:else}
        Vorbereitung … {suggestElapsed}s
      {/if}
      {#if liveStatus?.note}· {liveStatus.note}{/if}
    </p>
  {/if}
  {#if suggestResult}
    <div class="suggest">
      {#if analyzeInfo}<p class="muted">Analysiert mit: <span class="mono">{analyzeInfo.label}</span>
        {#if analyzeInfo.note}· {analyzeInfo.note}{/if}</p>{/if}
      <p class="muted">{suggestResult.reasoning}</p>
      <table>
        <thead><tr><th>Tier</th><th>Vorschlag</th><th>Preis in/out $/M</th><th>Variante</th><th>Begründung</th></tr></thead>
        <tbody>
          {#each ['fast', 'medium', 'heavy'] as tier}
            <tr>
              <td>{TIER_INFO[tier].title}</td>
              <td class="mono">{suggestResult[tier]?.model}</td>
              <td>{priceText(suggestResult[tier]?.model || '')}</td>
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
          <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
        {/each}
      </select>
      <select bind:value={src.variant}>
        {#each variantOptions(src.model) as v}<option value={v}>{v}</option>{/each}
      </select>
    </div>
    {#if tier === 'medium'}
      {#each [{ key: 'build', label: 'Build', sel: buildSel, hint: 'Standardvariante', eff: effBuild },
              { key: 'plan', label: 'Plan', sel: planSel, hint: 'höchste Variante', eff: effPlan }] as ag}
        <div class="tierRow subRow">
          <div class="tierLabel">
            <strong>↳ {ag.label}</strong>
            <span class="muted">
              {ag.sel.model ? `manuell · aktuell: ${ag.eff}` : `hergeleitet von @medium (${ag.hint}) · ${ag.eff || '—'}`}
            </span>
          </div>
          <select bind:value={ag.sel.model}>
            <option value="">hergeleitet von @medium</option>
            {#each goModels as m}
              <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
            {/each}
          </select>
          <select bind:value={ag.sel.variant} disabled={!ag.sel.model}>
            {#each variantOptions(ag.sel.model || medium.model) as v}<option value={v}>{v}</option>{/each}
          </select>
        </div>
      {/each}
    {/if}
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
    {:else if applyResult?.unchanged}
      <span class="muted">Keine Änderung — {applyResult.rollout}</span>
    {/if}
    {#if rolloutResult?.dispatched}<span class="muted">Rollout-Workflow gestartet.</span>{/if}
    {#if rolloutStatus?.run_number}
      <span class="badge {rolloutStatus.conclusion === 'success' ? 'active' : (rolloutStatus.conclusion === 'failure' ? 'err' : 'warn')}">
        Letzter Rollout #{rolloutStatus.run_number}: {rolloutStatus.conclusion || rolloutStatus.status}
      </span>
      {#if rolloutStatus.html_url}<a class="muted" href={rolloutStatus.html_url} target="_blank" rel="noreferrer">Log</a>{/if}
    {/if}
  </div>
  <p class="muted" style="margin-top:10px; border-top:1px solid var(--line); padding-top:10px">
    <strong>✔ Anwenden</strong> — speichert deine Auswahl im Repo (Commit) und rollt sie automatisch auf alle
    Container aus.<br />
    <strong>⏩ Force-Rollout</strong> — ändert nichts; startet nur die Container neu, damit sie den bereits
    gespeicherten Stand ziehen.<br />
    Neustart ~30–60 s; offene Sessions verbinden sich danach automatisch neu.
  </p>
</div>

<div class="card">
  <h2>
    Preisübersicht
    <label class="muted" style="font-weight:400; margin-left:12px">
      <input type="checkbox" bind:checked={catFilterOpenGoOnly} /> nur erlaubte Provider ({allowed.join(", ")})
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
          <td class="mono">{m.modelID}</td>
          <td><span class="badge {m.status === 'active' ? 'active' : 'warn'}">{m.status}</span>
            {#if !m.cost.input && !m.cost.output}<span class="badge active">gratis</span>{/if}</td>
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
  .subRow { padding-left: 22px; border-left: 2px solid var(--line); margin-left: 6px; }
  .tierLabel { display: flex; flex-direction: column; gap: 2px; }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
  .suggest { margin-top: 14px; }
  .errbox { border-color: var(--err); }
</style>
