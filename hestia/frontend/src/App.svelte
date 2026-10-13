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
let useFree = $state({ fast: false, medium: false })
// Ausfall-Modelle je Tier: {model, variant, disabled:true} = bewusst keines
let fallbacks = $state({
  fast: { model: '', variant: '(keine)', disabled: true },
  medium: { model: '', variant: '(keine)', disabled: true },
})
// true = vom Nutzer manuell gesetzt → Auto-Vorschlag (paidCounterpart) überschreibt nicht mehr
let fbManual = $state({ fast: false, medium: false })
let foStatus = $state(null)
let foBusy = $state(false)
// Auto-Failover-Einstellungen (POST /api/failover/settings)
let foSettings = $state({ enabled: true, threshold: 3, intervalSeconds: 300, timeoutSeconds: 30 })
let foSaveBusy = $state(false)
let foSaveResult = $state(null)
let accStatus = $state(null)
let accBusy = $state(false)

// Hermes-Agent (Nous Research): primäres Modell + Fallback-Kette aus der SOT
let hermesStatus = $state(null)
let hermesBusy = $state(false)
let hermesSel = $state('')
let hermesFb1 = $state('')
let hermesFb2 = $state('')
let hermesPool = $state(false)

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

// ── Automatik des KI-Vorschlags (GET /api/suggest/status, POST /api/suggest/settings) ──
let suggestStatus = $state(null)
let autoSaveBusy = $state(false)
let autoSaveResult = $state(null)
// Formular in Stunden (die API rechnet in Sekunden)
let autoForm = $state({ enabled: false, intervalHours: 24, cooldownHours: 24, analysisModel: '' })

const hoursToSec = (h) => Math.max(0, Math.round((Number(h) || 0) * 3600))
const secToHours = (s) => {
  const n = Number(s)
  if (!Number.isFinite(n) || n <= 0) return 0
  return Math.round((n / 3600) * 100) / 100
}

async function loadSuggestStatus() {
  try {
    const st = await api('/api/suggest/status')
    suggestStatus = st
    const a = st?.auto
    if (a) {
      autoForm = {
        enabled: !!a.enabled,
        intervalHours: secToHours(a.checkIntervalSeconds) || autoForm.intervalHours,
        cooldownHours: secToHours(a.cooldownSeconds) || autoForm.cooldownHours,
        analysisModel: typeof a.analysisModel === 'string' ? a.analysisModel : '',
      }
    }
  } catch { /* ignore */ }
}

async function saveAutoSettings() {
  autoSaveBusy = true
  autoSaveResult = null
  error = null
  try {
    const res = await api('/api/suggest/settings', {
      method: 'POST',
      body: {
        enabled: !!autoForm.enabled,
        checkIntervalSeconds: Math.max(900, hoursToSec(autoForm.intervalHours)),   // min 0,25 h
        cooldownSeconds: Math.max(3600, hoursToSec(autoForm.cooldownHours)),       // min 1 h
        analysisModel: autoForm.analysisModel || '',
      },
    })
    autoSaveResult = res
    await loadSuggestStatus()
  } catch (e) {
    autoSaveResult = { error: e.message }
    error = 'Automatik-Einstellungen: ' + e.message
  } finally {
    autoSaveBusy = false
  }
}

// Zeit-Hilfen für die Statuszeile
function agoText(ts) {
  if (!ts) return '—'
  const s = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)))
  if (s < 90) return `vor ${s} s`
  if (s < 5400) return `vor ${Math.round(s / 60)} min`
  if (s < 172800) return `vor ${Math.round(s / 3600)} h`
  return fmtTime(ts)
}
function durText(sec) {
  const n = Number(sec)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n < 90) return `${Math.round(n)} s`
  if (n < 5400) return `${Math.round(n / 60)} min`
  if (n % 3600 === 0) return `${Math.round(n / 3600)} h`
  return `${Math.floor(n / 3600)} h ${Math.round((n % 3600) / 60)} min`
}
function stampText(ts) {
  if (!ts) return '—'
  const d = new Date(Number(ts) * 1000)
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

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
    // Persistierte Ausfall-Modelle aus dem Repository in den Editor spiegeln.
    // Ohne gespeichertes Fallback gilt "bewusst kein Ausfall-Modell" (disabled).
    const fb = config.fallbacks || {}
    for (const tier of ['fast', 'medium']) {
      const c = fb[tier]
      fallbacks[tier] = c?.model
        ? { model: c.model, variant: c.variant || '(keine)', disabled: false }
        : { model: '', variant: '(keine)', disabled: true }
      fbManual[tier] = false
    }
  } catch (e) {
    error = e.message
  }
}

function modelOf(ref) {
  return catalog.models.find(m => m.ref === ref) || null
}

// Bezahltes Gegenstück zu einem Gratis-Modell — gleiche Heuristik wie im Backend:
// modelID ohne Suffix "-free", bevorzugt gleicher Provider, sonst "opencode-go".
function paidCounterpart(ref) {
  const m = modelOf(ref)
  if (!m || !isFree(ref)) return null
  const base = (m.modelID || '').replace(/-free$/, '')
  if (base === m.modelID) return null   // kein "-free"-Suffix → keine Ableitung
  const cands = catalog.models.filter(c => c.modelID === base && (c.cost.input || c.cost.output))
  if (!cands.length) return null
  return cands.find(c => c.providerID === m.providerID)
    || cands.find(c => c.providerID === 'opencode-go')
    || cands[0]
}

// Manuelle Modellwahl im Tier-Editor: bei einem Gratis-Modell (fast/medium)
// wird das bezahlte Gegenstück als Ausfall-Modell vorgeschlagen (sofern der
// Nutzer nicht selbst schon eines gewählt hat), sonst geräumt.
function onModelChange(tier, src) {
  src.variant = src.variant || '(keine)'
  if (tier !== 'fast' && tier !== 'medium') return
  if (fbManual[tier]) return
  if (isFree(src.model)) {
    const c = paidCounterpart(src.model)
    fallbacks[tier] = c
      ? { model: c.ref, variant: '(keine)', disabled: false }
      : { model: '', variant: '(keine)', disabled: true }
  } else {
    fallbacks[tier] = { model: '', variant: '(keine)', disabled: true }
  }
}

// Ausfall-Modell manuell wählen ("— kein Ausfall-Modell —" ⇒ disabled)
function onFallbackModelChange(tier) {
  fbManual[tier] = true
  const fb = fallbacks[tier]
  if (!fb.model) {
    fb.variant = '(keine)'
    fb.disabled = true
  } else {
    fb.disabled = false
    if (!(variantOptions(fb.model) || []).includes(fb.variant)) fb.variant = '(keine)'
  }
}

function onFallbackVariantChange(tier) {
  fbManual[tier] = true
  const fb = fallbacks[tier]
  fb.variant = fb.variant || '(keine)'
  fb.disabled = !fb.model
}

// Für /api/apply: leere Auswahl ⇒ bewusst kein Ausfall-Modell (disabled: true)
function fallbackNorm(tier) {
  const fb = fallbacks[tier] || {}
  if (!fb.model) return { model: '', variant: '', disabled: true }
  return {
    model: fb.model,
    variant: !fb.variant || fb.variant === '(keine)' ? '' : fb.variant,
    disabled: false,
  }
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

// ── gespeicherter Automatik-Vorschlag ──
const autoProposal = $derived(suggestStatus?.proposal || null)
const autoProposalResult = $derived(autoProposal?.result || null)

function tierChoiceOf(res, tier) {
  const d = res?.[tier]
  if (!d) return null
  return (useFree[tier] && d.free) ? (d.free || d.paid) : (d.paid || d.free || null)
}

// Weicht der Vorschlag von der aktiven (geladenen) Konfiguration ab?
const proposalDiffers = $derived.by(() => {
  const res = autoProposalResult
  if (!res) return false
  const cfg = config?.tiers || {}
  let seen = false
  for (const tier of ['fast', 'medium', 'heavy']) {
    const c = tierChoiceOf(res, tier)
    if (!c?.model) continue
    seen = true
    const cur = cfg[tier] || {}
    const curRef = (cur.model || '') + (cur.variant ? '#' + cur.variant : '')
    const newRef = c.model + (c.variant ? '#' + c.variant : '')
    if (curRef !== newRef) return true
  }
  return seen ? false : false
})

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
    loadSuggestStatus()
  }
}

function acceptSuggestion(res = suggestResult) {
  for (const tier of ['fast', 'medium', 'heavy']) {
    const d = res?.[tier]
    if (!d) continue
    const chosen = (useFree[tier] && d.free) ? d.free : d.paid
    const other = (useFree[tier] && d.free) ? d.paid : d.free
    if (!chosen) continue
    const src = { fast, medium, heavy }[tier]
    src.model = chosen.model
    src.variant = chosen.variant || '(keine)'
    // die nicht gewählte Variante als Ausfall-Modell hinterlegen (fast/medium)
    if (tier !== 'heavy' && other?.model) {
      fallbacks[tier] = { model: other.model, variant: other.variant || '(keine)', disabled: false }
    } else {
      fallbacks[tier] = { model: '', variant: '(keine)', disabled: true }
    }
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
      fallbacks: {
        fast: fallbackNorm('fast'),
        medium: fallbackNorm('medium'),
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

async function loadFailover() {
  try {
    const st = await api('/api/failover/status')
    foStatus = st
    // Formular aus dem Server-Stand vorbefüllen
    foSettings = {
      enabled: !!st?.autoFailover,
      threshold: Number(st?.threshold) || foSettings.threshold,
      intervalSeconds: Number(st?.interval) || foSettings.intervalSeconds,
      timeoutSeconds: Number(st?.timeout) || foSettings.timeoutSeconds,
    }
  } catch { /* ignore */ }
}

// Auto-Failover-Einstellungen persistieren (SOT des Backends)
async function saveFailoverSettings() {
  foSaveBusy = true
  foSaveResult = null
  error = null
  try {
    const res = await api('/api/failover/settings', {
      method: 'POST',
      body: {
        enabled: !!foSettings.enabled,
        threshold: Number(foSettings.threshold) || 1,
        intervalSeconds: Number(foSettings.intervalSeconds) || 60,
        timeoutSeconds: Number(foSettings.timeoutSeconds) || 5,
      },
    })
    foSaveResult = res
    await loadFailover()
  } catch (e) {
    foSaveResult = { error: e.message }
    error = 'Ausfallschutz-Einstellungen: ' + e.message
  } finally {
    foSaveBusy = false
  }
}

async function runFailoverCheck() {
  foBusy = true
  error = null
  try {
    foStatus = { ...(foStatus || {}), ...(await api('/api/failover/check', { body: {}, method: 'POST' })) }
    await loadFailover()
  } catch (e) {
    error = 'Ausfallschutz: ' + e.message
  } finally {
    foBusy = false
  }
}

// Budget-System (OpenCode Go): Konten + Verbrauchsfenster (Anzeige + Kontowechsel)
async function loadAccounts() {
  try { accStatus = await api('/api/accounts') } catch { /* ignore */ }
}

async function runAccountsCheck() {
  accBusy = true
  error = null
  try {
    accStatus = { ...(accStatus || {}), ...(await api('/api/accounts/check', { body: {}, method: 'POST' })) }
    await loadAccounts()
  } catch (e) {
    error = 'Budget: ' + e.message
  } finally {
    accBusy = false
  }
}

// Konto wechseln: dispatcht switch-account.yml; die OpenCode-Services der Container starten neu
async function runAccountsSwitch(id) {
  if (!id) return
  if (!confirm(`Konto auf "${id}" wechseln?\n\nDer OpenCode-Service der Container wird neu gestartet — laufende Sessions brechen dabei ab.`)) return
  accBusy = true
  error = null
  try {
    await api('/api/accounts/switch', { body: { id } })
    await loadAccounts()
  } catch (e) {
    error = 'Wechsel: ' + e.message
  } finally {
    accBusy = false
  }
}

const WIN_LABELS = { rolling: '5 h', weekly: 'Woche', monthly: 'Monat' }

// Farbe nach Restbudget; Schwellen kommen aus der Config (.opencode/hestia-accounts.jsonc → display)
function barColor(free, display) {
  const warn = display?.warnBelowPercentFree ?? 50
  const crit = display?.criticalBelowPercentFree ?? 10
  return free < crit ? 'var(--err)' : (free < warn ? 'var(--warn)' : 'var(--ok)')
}

function fmtReset(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return 'Reset ' + d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
}

// Hermes-Steuerung: SOT (.opencode/hestia-hermes.jsonc) + Health des Containers
function hermesRef(entry) {
  if (!entry) return ''
  return entry.provider && entry.model ? `${entry.provider}/${entry.model}` : (entry.model || '')
}
async function loadHermes() {
  try {
    const st = await api('/api/hermes')
    hermesStatus = st
    const cfg = st?.config || {}
    hermesSel = hermesRef(cfg.primary) || hermesSel
    const fb = cfg.fallback || []
    hermesFb1 = hermesRef(fb[0]) || hermesFb1
    hermesFb2 = hermesRef(fb[1]) || hermesFb2
    hermesPool = !!cfg?.pool?.followActive
  } catch { /* ignore */ }
}

async function runHermesApply() {
  if (!hermesSel) { error = 'Hermes: kein Primary-Modell gewählt'; return }
  if (!confirm(`Hermes auf "${hermesSel}" umstellen?\n\nAnwenden aktualisiert /opt/data/config.yaml und startet Hermes neu — laufende Hermes-Sessions brechen dabei ab.`)) return
  hermesBusy = true
  error = null
  try {
    const toEntry = (ref) => ref ? { provider: ref.split('/', 1)[0], model: ref.slice(ref.indexOf('/') + 1) } : null
    const fallback = [hermesFb1, hermesFb2].filter(Boolean).map(toEntry)
    const res = await api('/api/hermes/apply', {
      method: 'POST',
      body: { primary: toEntry(hermesSel), fallback, pool: { followActive: hermesPool } },
    })
    hermesStatus = { ...(hermesStatus || {}), config: res.config || hermesStatus?.config }
    await loadHermes()
  } catch (e) {
    error = 'Hermes: ' + e.message
  } finally {
    hermesBusy = false
  }
}

load()
loadRolloutStatus()
loadFailover()
loadAccounts()
loadHermes()
loadSuggestStatus()
setInterval(loadAccounts, 60000)
setInterval(loadHermes, 60000)
setInterval(loadSuggestStatus, 60000)
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

<!-- gemeinsame Vorschlags-Darstellung (manueller Lauf + gespeicherter Automatik-Vorschlag) -->
{#snippet suggestTable(res, btnLabel = '↓ In den Editor übernehmen')}
  {#if res.reasoning}<p class="muted">{res.reasoning}</p>{/if}
  <table>
    <thead><tr><th>Tier</th><th>Wahl</th><th>Modell</th><th>Preis in/out $/M</th><th>Variante</th><th>Begründung</th></tr></thead>
    <tbody>
      {#each ['fast', 'medium', 'heavy'] as tier}
        {@const paid = res[tier]?.paid}
        {@const free = res[tier]?.free}
        <tr class={!useFree[tier] ? 'rowActive' : ''}>
          <td rowspan={free ? 2 : 1} class="tierCell">{TIER_INFO[tier].title}</td>
          <td>
            <button class="chip {!useFree[tier] ? 'chipActive' : ''}" onclick={() => (useFree[tier] = false)}>
              bezahlt
            </button>
          </td>
          <td class="mono">{paid?.model}</td>
          <td>{priceText(paid?.model || '')}</td>
          <td class="muted">{paid?.variant || '—'}</td>
          <td class="muted">{paid?.reason}</td>
        </tr>
        {#if free}
          <tr class={useFree[tier] ? 'rowActive' : ''}>
            <td>
              <button class="chip {useFree[tier] ? 'chipActive' : ''}" onclick={() => (useFree[tier] = true)}>
                gratis
              </button>
            </td>
            <td class="mono">{free.model}</td>
            <td>{priceText(free.model)}</td>
            <td class="muted">{free.variant || '—'}</td>
            <td class="muted">{free.reason}</td>
          </tr>
        {/if}
      {/each}
    </tbody>
  </table>
  <div class="row">
    <button onclick={() => acceptSuggestion(res)}>{btnLabel}</button>
  </div>
{/snippet}

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
          {#if tier === 'medium' && config.primaryAgents}
            {#each ['build', 'plan'] as agent}
              {@const pa = config.primaryAgents[agent]}
              {#if pa?.model}
                <tr>
                  <td class="subLabel">↳ {agent === 'build' ? 'Build' : 'Plan'}
                    <span class="badge">{pa.manual ? 'manuell' : 'hergeleitet'}</span>
                  </td>
                  <td class="mono">{pa.model}</td>
                  <td class="muted">{pa.variant || '—'}</td>
                  <td>{priceText(pa.model)}</td>
                </tr>
              {/if}
            {/each}
          {/if}
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
  <h2>
    Hermes — Modell/Provider
    <label class="muted" style="font-weight:400; margin-left:12px">
      {#if hermesStatus?.reachable && hermesStatus?.health?.ok}
        <span class="badge active">ok</span>
      {:else if hermesStatus?.reachable}
        <span class="badge warn">health: {hermesStatus?.health?.error || 'kein ok'}</span>
      {:else}
        <span class="badge err">nicht erreichbar</span>
      {/if}
      {#if hermesStatus?.health?.version}<span class="mono muted">v{hermesStatus.health.version}</span>{/if}
      · SOT: <span class="mono">.opencode/hestia-hermes.jsonc</span>
      · Workflow: <span class="mono">{hermesStatus?.workflow || '—'}</span>
    </label>
  </h2>

  <div class="row" style="margin-top:0">
    <label>Primary&nbsp;
      <select bind:value={hermesSel} disabled={hermesBusy}>
        <option value="" disabled>Modell wählen…</option>
        {#each goModels as m}
          <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
        {/each}
      </select>
    </label>
  </div>
  <div class="row">
    <label>Fallback 1&nbsp;
      <select bind:value={hermesFb1} disabled={hermesBusy}>
        <option value="">— keiner —</option>
        {#each goModels as m}
          <option value={m.ref}>{m.name} · {m.modelID}</option>
        {/each}
      </select>
    </label>
    <label>Fallback 2&nbsp;
      <select bind:value={hermesFb2} disabled={hermesBusy}>
        <option value="">— keiner —</option>
        {#each goModels as m}
          <option value={m.ref}>{m.name} · {m.modelID}</option>
        {/each}
      </select>
    </label>
  </div>
  <div class="row">
    <label>
      <input type="checkbox" bind:checked={hermesPool} disabled={hermesBusy} />
      Abo folgt dem aktiven Konto
    </label>
    <span class="muted">
      Restart: {hermesStatus?.config?.restart === false ? 'nein' : 'ja'} ·
      Workflow {hermesStatus?.workflow || '—'} schreibt config.yaml/.env und startet Hermes neu
    </span>
  </div>
  <div class="row">
    <span class="muted">Profile:
      {#if !Object.keys(hermesStatus?.config?.profiles || {}).length}
        <span class="mono">nur das Default-Profil</span>
      {:else}
        {#each Object.entries(hermesStatus.config.profiles) as [name, prof]}
          <span class="badge">{name}</span>
          <span class="mono">
            {prof?.primary?.provider || '—'}/{prof?.primary?.model || '—'}
          </span>
          · {(prof?.fallback || []).length} Fallback
          · {prof?.pool?.followActive ? 'Pool folgt aktivem Konto' : 'Pool fest'}
          {#if !prof?.enabled}<span class="badge warn">deaktiviert</span>{/if}
          <br />
        {/each}
      {/if}
    </span>
  </div>
  <div class="row">
    <button class="primary" onclick={runHermesApply} disabled={hermesBusy || !hermesSel}>
      {hermesBusy ? 'Wende an …' : 'Speichern & anwenden'}
    </button>
    {#if hermesStatus?.configError}<span class="muted" style="color:var(--err)">⚠ {hermesStatus.configError}</span>{/if}
  </div>
  <p class="muted" style="margin-top:8px">
    Anwenden aktualisiert <span class="mono">/opt/data/config.yaml</span> und startet Hermes neu
    (laufende Hermes-Sessions brechen ab). SOT: <span class="mono">.opencode/hestia-hermes.jsonc</span>.
    Weitere Profile (schreibgeschützt) bekommen ihre eigenen Dateien unter <span class="mono">/opt/data/profiles/&lt;name&gt;/</span>.
  </p>
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
      {@render suggestTable(suggestResult)}
    </div>
  {/if}

  <!-- ── Automatik ─────────────────────────────────────────────── -->
  <div class="autoBox">
    <h3>⏱ Automatik</h3>
    <p class="muted">
      Läuft nur, wenn sich Preise/Modelle ändern; max. 1 Lauf je Cooldown. Manueller Lauf nutzt weiter @heavy.
    </p>

    {#if autoProposal}
      <div class="proposalHead">
        <strong>Automatischer Vorschlag vom {stampText(autoProposal.generatedAt)}</strong>
        <span class="mono muted">Modell {autoProposal.model || '—'}</span>
        {#if proposalDiffers}
          <span class="badge warn">weicht ab</span>
        {:else}
          <span class="badge active">unverändert</span>
        {/if}
      </div>
      {#if autoProposalResult}
        <div class="suggest" style="margin-top:6px">
          {@render suggestTable(autoProposalResult, '↓ In den Editor übernehmen')}
        </div>
        <p class="muted" style="margin-top:4px">
          Übernehmen füllt nur den Tier-Editor — speichern musst du selbst mit „✔ Anwenden“.
        </p>
      {:else}
        <p class="muted">Kein Ergebnis im gespeicherten Vorschlag enthalten.</p>
      {/if}
    {:else}
      <p class="muted">Noch kein automatischer Vorschlag vorhanden.</p>
    {/if}

    <p class="muted" style="margin-top:8px">
      Automatik: <strong>{suggestStatus?.auto?.enabled ? 'an' : 'aus'}</strong>
      · letzte Prüfung {agoText(suggestStatus?.auto?.lastCheck)}
      · Änderung erkannt: {stampText(suggestStatus?.auto?.lastChange)}
      · nächste Prüfung in {durText(suggestStatus?.auto?.nextCheckIn)}
      {#if suggestStatus?.auto?.fingerprint}· Fingerprint <span class="mono">{suggestStatus.auto.fingerprint}</span>{/if}
    </p>

    <div class="row" style="margin-top:8px">
      <label class="foCheck">
        <input type="checkbox" bind:checked={autoForm.enabled} disabled={autoSaveBusy} />
        Automatik (nur bei Katalogänderung)
      </label>
      <label>
        Prüfintervall (Stunden)
        <input type="number" min="0.25" step="0.25" bind:value={autoForm.intervalHours} disabled={autoSaveBusy} />
      </label>
      <label>
        Cooldown (Stunden)
        <input type="number" min="1" step="1" bind:value={autoForm.cooldownHours} disabled={autoSaveBusy} />
      </label>
    </div>
    <div class="row">
      <label>
        Analyse-Modell (Automatik)
        <select bind:value={autoForm.analysisModel} disabled={autoSaveBusy}>
          <option value="">— @medium-Fallback —</option>
          {#each goModels as m}
            <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
          {/each}
        </select>
      </label>
      <button onclick={saveAutoSettings} disabled={autoSaveBusy}>
        {autoSaveBusy ? 'Speichere …' : '💾 Automatik speichern'}
      </button>
      {#if autoSaveResult?.saved}
        <span class="muted">
          Gespeichert {#if autoSaveResult.commit?.sha}· Commit <span class="mono">{autoSaveResult.commit.sha.slice(0, 8)}</span>{/if}
        </span>
      {:else if autoSaveResult?.error}
        <span class="muted" style="color:var(--err)">⚠ {autoSaveResult.error}</span>
      {/if}
    </div>
  </div>
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
      <select bind:value={src.model} onchange={() => onModelChange(tier, src)}>
        <option value="" disabled>Modell wählen…</option>
        {#each goModels as m}
          <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
        {/each}
      </select>
      <select bind:value={src.variant}>
        {#each variantOptions(src.model) as v}<option value={v}>{v}</option>{/each}
      </select>
    </div>
    {#if tier === 'fast' || tier === 'medium'}
      <div class="tierRow fbRow">
        <div class="tierLabel">
          <strong><span class="fireMark">🛡</span> Ausfallschutz</strong>
          <span class="muted">
            {#if fbManual[tier]}manuell gewählt{:else}Vorschlag vom Tier-Modell{/if}
            · greift automatisch bei Fehlern
          </span>
        </div>
        <select
          bind:value={fallbacks[tier].model}
          onchange={() => onFallbackModelChange(tier)}
        >
          <option value="">— kein Ausfall-Modell —</option>
          {#each goModels as m}
            <option value={m.ref}>{m.name} · {m.modelID} · {priceText(m.ref)}{isFree(m.ref) ? ' · gratis' : ''}</option>
          {/each}
        </select>
        <select
          bind:value={fallbacks[tier].variant}
          onchange={() => onFallbackVariantChange(tier)}
          disabled={!fallbacks[tier].model || fallbacks[tier].disabled}
        >
          {#each variantOptions(fallbacks[tier].model) as v}<option value={v}>{v}</option>{/each}
        </select>
      </div>
      {#if isFree(src.model) && !fallbacks[tier].model}
        <p class="muted fbHint warn">
          Gratis-Modell ohne Ausfall-Modell – bei Ausfall bleibt das Tier ohne Ersatz
        </p>
      {/if}
    {/if}
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
  <div class="row" style="margin-top:6px">
    <button onclick={runFailoverCheck} disabled={foBusy}>
      {foBusy ? 'Prüfe Modelle …' : '🛡 Ausfallschutz prüfen'}
    </button>
    {#if foStatus}
      <span class="muted">
        Auto-Failover: {foStatus.autoFailover ? 'an' : 'aus'} · Schwelle {foStatus.threshold}
        {#if foStatus.lastRun}· letzte Prüfung vor {Math.round((Date.now() / 1000) - foStatus.lastRun)}s{/if}
      </span>
    {/if}
  </div>
  {#if foStatus?.fallbacks && (foStatus.fallbacks.fast || foStatus.fallbacks.medium)}
    <p class="muted" style="margin-top:4px">
      Ausfall-Modelle:
      {#if foStatus.fallbacks.fast}fast → <span class="mono">{foStatus.fallbacks.fast.model}{foStatus.fallbacks.fast.variant ? '#' + foStatus.fallbacks.fast.variant : ''}</span>{/if}
      {#if foStatus.fallbacks.medium} · medium → <span class="mono">{foStatus.fallbacks.medium.model}{foStatus.fallbacks.medium.variant ? '#' + foStatus.fallbacks.medium.variant : ''}</span>{/if}
    </p>
  {/if}
  {#if foStatus?.lastResults}
    <p class="muted" style="margin-top:2px">
      Zustand:
      {#each Object.entries(foStatus.lastResults) as [tier, r]}
        <span class="badge {r.ok ? 'active' : 'err'}">{tier} {r.ok ? 'ok' : 'AUSFALL'}</span>
      {/each}
    </p>
  {/if}

  <div class="foBox">
    <h3>🛡 Ausfallschutz (Einstellungen)</h3>
    <p class="muted">
      Der Failover-Wächter prüft die Tiers selbstständig und schaltet bei gehäuften Fehlern auf das
      Ausfall-Modell um — die Auswahl oben (je Tier) wird mit „Anwenden“ gespeichert, diese
      Werte hier im Backend.
    </p>
    <div class="row" style="margin-top:8px">
      <label class="foCheck">
        <input type="checkbox" bind:checked={foSettings.enabled} disabled={foSaveBusy} />
        Auto-Failover aktiv
      </label>
      <label>
        Fehlerschwelle
        <input type="number" min="1" max="10" step="1" bind:value={foSettings.threshold} disabled={foSaveBusy} />
      </label>
      <label>
        Prüfintervall (Sekunden)
        <input type="number" min="60" step="10" bind:value={foSettings.intervalSeconds} disabled={foSaveBusy} />
      </label>
      <label>
        Probe-Timeout (Sekunden)
        <input type="number" min="5" max="300" step="5" bind:value={foSettings.timeoutSeconds} disabled={foSaveBusy} />
      </label>
    </div>
    <div class="row">
      <button onclick={saveFailoverSettings} disabled={foSaveBusy}>
        {foSaveBusy ? 'Speichere …' : '💾 Einstellungen speichern'}
      </button>
      {#if foSaveResult?.saved}
        <span class="muted">
          Gespeichert {#if foSaveResult.commit?.sha}· Commit <span class="mono">{foSaveResult.commit.sha.slice(0, 8)}</span>{/if}
        </span>
      {:else if foSaveResult?.error}
        <span class="muted" style="color:var(--err)">⚠ {foSaveResult.error}</span>
      {/if}
      <span class="muted">
        Status: {foStatus?.autoFailover ? 'aktiv' : 'inaktiv'}
        · Schwelle {foStatus?.threshold ?? '—'}
        · alle {foStatus?.interval ?? '—'}s · Timeout {foStatus?.timeout ?? '—'}s
      </span>
    </div>
  </div>
</div>

<div class="card">
  <h2>
    OpenCode Go — Budget
    <label class="muted" style="font-weight:400; margin-left:12px">
      Modus {accStatus?.mode || '—'} · aktiv: <span class="mono">{accStatus?.active || '—'}</span>
      {#if accStatus?.lastRun}· zuletzt {fmtTime(accStatus.lastRun)}{/if}
      {#if accStatus?.lastSwitch}· letzter Wechsel {fmtTime(accStatus.lastSwitch)}{/if}
    </label>
  </h2>

  <div class="row" style="margin-top:0">
    <button onclick={runAccountsCheck} disabled={accBusy}>
      {accBusy ? 'Frage ab …' : '⟳ Jetzt prüfen'}
    </button>
    <span class="muted">
      Abfrage alle {accStatus?.interval || 300}s ·
      {accStatus?.mode === 'auto' ? 'Automatik aktiv (Wechsel bei erschöpftem Abo)'
        : accStatus?.mode === 'notify' ? 'nur Meldung (Wechsel per Button)' : 'nur Anzeige'} ·
      Schwellen und Policy: <span class="mono">.opencode/hestia-accounts.jsonc</span>
    </span>
  </div>

  {#if accStatus?.policy?.expiring?.enabled}
    <p class="muted" style="margin:6px 0 0">
      Ablauf-Bevorzugung aktiv: Ein Abo wird bevorzugt leergefahren, sobald ein Fenster in
      ≤ {accStatus.policy.expiring.withinHours} h zurücksetzt und noch ≥ {accStatus.policy.expiring.minRemainingPercent} % frei ist.
    </p>
  {/if}

  {#if accStatus?.error}
    <p class="muted" style="color:var(--err)">⚠ {accStatus.error}</p>
  {/if}

  {#if accStatus?.accounts?.length}
    {@const expNow = accStatus.accounts.find(a => a.expiring && a.isActive)}
    {#if expNow}
      <p class="expiryNote">⚡ <strong>{expNow.label}</strong> wird jetzt bevorzugt leergefahren — Reset in
        {Math.round(expNow.expiring.hoursLeft)} h ({WIN_LABELS[expNow.expiring.window]}), noch {Math.round(expNow.expiring.free)} % frei.</p>
    {/if}
    {#each accStatus.accounts as acc (acc.id)}
      {@const deadWins = Object.entries(acc.windows || {}).filter(([, x]) => x.status === 'rate-limited').map(([k]) => WIN_LABELS[k])}
      {@const disp = accStatus?.display || {}}
      <div class="subRow {acc.expiring ? 'expiring' : ''}{acc.expiring && acc.isActive ? ' expiring-now' : ''}" style="margin-top:10px">
        <div class="row" style="margin-top:0">
          <strong>{acc.label}</strong>
          <span class="mono muted">{acc.id}</span>
          {#if acc.isActive}<span class="badge active">aktiv</span>{/if}
          {#if !acc.hasKey}<span class="badge warn">kein Key</span>{/if}
          {#if acc.error}<span class="badge err">{acc.error}</span>{/if}
          {#if deadWins.length}<span class="badge err">erschöpft: {deadWins.join(', ')}</span>{/if}
          {#if acc.expiring}
            <span class="badge expiry">⚡ {acc.isActive ? 'wird jetzt leergefahren' : 'bald fällig'} · Reset in {Math.round(acc.expiring.hoursLeft)} h ({WIN_LABELS[acc.expiring.window]})</span>
          {/if}
          {#if !acc.isActive && acc.ok}
            <button class="mini" onclick={() => runAccountsSwitch(acc.id)} disabled={accBusy}
              title="Konto wechseln (Service-Neustart)">⇄ wechseln</button>
          {/if}
        </div>
        {#each ['rolling', 'weekly', 'monthly'] as w (w)}
          {@const win = acc.windows?.[w]}
          {#if win}
            {@const fillPct = Math.max(0, Math.min(100, disp.barMetric === 'free' ? win.free : win.used))}
            <div class="budgetBar" title={fmtReset(win.resetsAt)}>
              <span class="budgetName">{WIN_LABELS[w]}</span>
              <span class="budgetTrack">
                <span class="budgetFill" style="width:{fillPct}%; background:{barColor(win.free, disp)}"></span>
              </span>
              <span class="muted">
                {Math.round(win.used)} % verbraucht · {Math.round(win.free)} % frei
                {#if win.status === 'rate-limited'}<span class="badge err">gesperrt</span>{/if}
                {#if fmtReset(win.resetsAt)} · {fmtReset(win.resetsAt)}{/if}
              </span>
            </div>
          {/if}
        {/each}
      </div>
    {/each}
  {:else}
    <p class="muted">Noch keine Kontendaten — SOT: <span class="mono">.opencode/hestia-accounts.jsonc</span></p>
  {/if}

  {#if accStatus?.recommendation?.switchRecommended}
    <p class="muted" style="margin-top:10px; border-top:1px solid var(--line); padding-top:8px">
      💡 Wechsel empfohlen → <span class="mono">{accStatus.recommendation.target}</span>
      ({accStatus.recommendation.reason})
      <button class="mini" onclick={() => runAccountsSwitch(accStatus.recommendation.target)} disabled={accBusy}>⇄ jetzt wechseln</button>
    </p>
  {/if}
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
  .fbHint { margin: -4px 0 10px 2px; font-size: .78rem; }
  .fbHint.warn { color: var(--err); }
  /* Ausfallschutz-Zeile (je Tier) + Einstellungsblock */
  .fbRow {
    margin: -4px 0 10px 6px; padding: 8px 10px; border-radius: 8px;
    border: 1px dashed var(--line); background: var(--panel-2);
  }
  .fbRow .tierLabel strong { color: var(--fire-soft); font-size: .82rem; }
  .fireMark { filter: saturate(1.2); }
  .fbRow select { font-size: .8rem; }
  .foBox {
    margin-top: 14px; padding: 12px; border: 1px solid var(--line);
    border-left: 3px solid var(--fire); border-radius: 8px; background: var(--panel-2);
  }
  .foBox h3 { margin: 0 0 6px; font-size: .9rem; color: var(--fire-soft); }
  .foBox p { margin: 0; }
  .foBox label { display: inline-flex; align-items: center; gap: 6px; font-size: .8rem; color: var(--muted); }
  .foBox input[type=number] { width: 86px; }
  .foCheck { color: var(--text) !important; font-weight: 600; }
  .row { display: flex; gap: 10px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
  /* Automatik-Block der KI-Vorschlag-Karte */
  .autoBox {
    margin-top: 16px; padding: 12px; border: 1px solid var(--line);
    border-left: 3px solid var(--fire); border-radius: 8px; background: var(--panel-2);
  }
  .autoBox h3 { margin: 0 0 6px; font-size: .9rem; color: var(--fire-soft); }
  .autoBox p { margin: 0; }
  .autoBox label { display: inline-flex; align-items: center; gap: 6px; font-size: .8rem; color: var(--muted); }
  .autoBox input[type=number] { width: 86px; }
  .autoBox select { max-width: 460px; }
  .proposalHead {
    display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); font-size: .85rem;
  }
  .suggest { margin-top: 14px; }
  .errbox { border-color: var(--err); }
  .tierCell { vertical-align: top; white-space: nowrap; }
  .subLabel { padding-left: 18px; border-left: 2px solid var(--line); }
  .chip {
    padding: 3px 11px; border-radius: 999px; border: 1px solid var(--line);
    background: var(--panel-2); color: var(--muted); font-size: .72rem;
    cursor: pointer; line-height: 1.4;
  }
  .chip:hover { border-color: var(--fire); color: var(--text); }
  .chipActive { background: var(--fire); border-color: var(--fire); color: #1a120a; font-weight: 600; }
  .rowActive td { background: rgba(224, 115, 29, 0.06); }
  .budgetBar { display: grid; grid-template-columns: 64px 120px 1fr; gap: 8px; align-items: center; margin-top: 5px; font-size: .78rem; }
  .budgetName { color: var(--muted); }
  .budgetTrack { display: block; height: 8px; border-radius: 999px; background: var(--panel-2); border: 1px solid var(--line); overflow: hidden; }
  .budgetFill { display: block; height: 100%; border-radius: 999px; }
  .mini { padding: 1px 6px; font-size: .78rem; }
  /* Abo läuft bald ab (policy.expiring): Konto hervorheben */
  .subRow.expiring { border-left-color: var(--warn); background: var(--panel-2); border-radius: 0 8px 8px 0; }
  .subRow.expiring-now { animation: expiryPulse 2.4s ease-in-out infinite; }
  .badge.expiry { border-color: var(--warn); background: var(--warn); color: #1e1813; font-weight: 600; }
  .expiryNote { margin: 8px 0 0; padding: 8px 10px; border: 1px solid var(--warn); border-radius: 8px; background: var(--panel-2); }
  @keyframes expiryPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(217, 161, 58, 0); }
    50% { box-shadow: 0 0 0 4px rgba(217, 161, 58, .22); }
  }
</style>
