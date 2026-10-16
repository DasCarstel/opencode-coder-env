// ntfy-notify.js — OpenCode V2 → ntfy (selbst gehosteter Notification-Hub)
//
// Meldet an das Topic "ai":
//   session.idle      → Agent fertig (3) + TLDR der letzten Antwort
//   form.created      → Agent hat eine Frage (4), inkl. Optionen
//   permission.asked  → Freigabe nötig (4)
//   session.error / session.step.failed → Fehler (5/4)
//
// V2-Plugin (Plugin-Definition mit id + setup(ctx)); V1-Plugins laufen in
// OpenCode 2.x nicht mehr. Events kommen aus ctx.event.subscribe(), das
// "arbeitet gerade"-Signal zusätzlich aus dem ctx.session.hook("prompt")-Hook.
//
// Echte V2-Event-Form (empirisch gegen OpenCode 2.0.29 verifiziert):
//   { id, created, type, durable: { aggregateID: "ses_…" },
//     location: { directory: "/home/…/workspaces/<ws>/<projekt>" },
//     data: { sessionID: "ses_…", … } }
// Also: Session-ID aus data.sessionID (Fallback durable.aggregateID),
// Projekt-Label aus location.directory, Text aus session.text.ended.
//
// Aktiv NUR im Hintergrund-Service (`opencode serve …`): TUI-, `run`- und
// Web-Prozesse laden dasselbe Plugin, würden aber dieselben Events doppelt
// melden. Das Modul ist ein Singleton je Prozess — Zustand (working/Dedup)
// wird über alle Plugin-Instanzen (eine je Location) geteilt, damit ein
// Event, das mehrere Instanzen sehen, nur eine Nachricht erzeugt.
//
// Umgebungsvariablen:
//   NTFY_URL    Basis-URL        (Default https://ntfy.mueller-nas.de)
//   NTFY_TOKEN  Bearer-Token     (ohne Token bleibt das Plugin still)
//   NTFY_TOPIC  Topic            (Default "ai")
//   NTFY_DEBUG=1      Debug-Ausgaben ins OpenCode-Log
//   NTFY_SELFTEST=1   sendet beim Laden genau eine Testnachricht (Diagnose),
//                     unabhängig vom Prozess-Check
//   NTFY_SESSION_PORT Port der laufenden `opencode serve`-Web-UI für den
//                     Klick-Link (Default 49374)
//   NTFY_SESSION_URL  feste Session-URL — schlägt die automatische Ableitung
//
// Antwort-Brücke (der Nutzer antwortet in der ntfy-App auf eine Meldung; diese
// Antwort landet als normale Nachricht im Topic und wird hier abgeholt und als
// Prompt in die zuletzt benachrichtigte Session eingespeist):
//   NTFY_REPLY_ENABLED      "0"/"false" schaltet die Brücke ab (Default: an)
//   NTFY_REPLY_TOPIC        Topic zum Abholen (Default NTFY_TOPIC, also "ai")
//   NTFY_REPLY_POLL_SECONDS Zeitlimit einer Abfrage in Sekunden (Default 60)
//
// Installation: coder-templates/shared/plugins/ntfy-notify.js
//               → ~/.config/opencode/plugins/ (dort automatisch geladen)

import { readFileSync } from "node:fs";

const DEFAULT_URL = "https://ntfy.mueller-nas.de";
const DEFAULT_TOPIC = "ai";
const MAX_TITLE = 90;
const MAX_BODY = 500;
// Frage- und Freigabe-Meldungen tragen eine mehrzeilige Optionsliste — ntfy
// erlaubt bis 4096 Byte je Message, hier reichen 1500 Zeichen mit Puffer.
const MAX_QUESTION_BODY = 1500;
// Lange Optionstexte je Option kürzen, damit die Liste lesbar bleibt.
const MAX_OPTION_LEN = 120;
const COOLDOWN_MS = 15000;
const PLUGIN_ID = "das.ntfy-notify";
// Antwort-Brücke: eigene Präfixe, Abfrage- und Wartezeiten.
const REPLY_PREFIXES = ["❓", "✅", "⚠️", "🔐", "🔔", "↩️"];
const REPLY_ACK_TITLE = "↩️ Antwort an Agenten weitergegeben";
const REPLY_NO_SESSION_TITLE = "⚠️ Antwort ohne Ziel-Session";
const REPLY_POLL_SECONDS = 60;
// poll=1 liefert sofort zurück — zwischen zwei Abfragen kurz warten, damit
// daraus kein Hot-Loop gegen den Hub wird.
const REPLY_IDLE_MS = 5000;
const REPLY_ERROR_MS = 5000;
const MAX_REPLY_TEXT = 2000;
const MAX_SENT_IDS = 200;
// Schon verarbeitete Fremd-IDs — gleiche Begrenzung wie bei den eigenen IDs.
const MAX_PROCESSED_IDS = 200;

// ── Hilfsfunktionen ────────────────────────────────────────────────────────

/**
 * Token/URL auch aus der Workspace-Env-Datei lesen.
 *
 * `startup.sh` schreibt `NTFY_TOKEN`/`NTFY_URL` nach `/etc/opencode.env`
 * (Mode 0600) — die Service-Umgebung über `opencode service set env` kommt
 * nicht in jeder Installation im Service-Prozess an. Env-Variablen haben
 * Vorrang, danach die Datei, dann die Defaults.
 */
function readEnvFile(path) {
  const out = {};
  try {
    for (const line of readFileSync(path, "utf8").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const index = trimmed.indexOf("=");
      if (index < 1) continue;
      out[trimmed.slice(0, index).trim()] = trimmed
        .slice(index + 1)
        .trim()
        .replace(/^"(.*)"$/, "$1");
    }
  } catch {
    /* Datei fehlt – dann bleiben nur die Env-Variablen */
  }
  return out;
}

function resolveConfig(env) {
  const needsFile = !(env.NTFY_TOKEN && env.NTFY_URL);
  const file = needsFile ? readEnvFile(env.NTFY_ENV_FILE ?? "/etc/opencode.env") : {};
  return {
    token: env.NTFY_TOKEN || file.NTFY_TOKEN || "",
    url: env.NTFY_URL || file.NTFY_URL || DEFAULT_URL,
    topic: env.NTFY_TOPIC || file.NTFY_TOPIC || DEFAULT_TOPIC,
  };
}

function debug(...args) {
  if (process.env.NTFY_DEBUG === "1" || process.env.NTFY_SELFTEST === "1") {
    console.error("[ntfy-notify]", ...args);
  }
}

/** Nur der Hintergrund-Service soll melden — sonst doppelte Events aus TUI/CLI. */
export function isServiceProcess(argv = process.argv.slice(2)) {
  const args = [...argv];
  const separator = args.indexOf("--");
  if (separator !== -1) args.splice(separator);
  while (
    args[0] === "--print-logs" ||
    args[0] === "--log-level" ||
    args[0]?.startsWith("--log-level=")
  ) {
    args.splice(0, args[0] === "--log-level" ? 2 : 1);
  }
  return args[0] === "serve";
}

function clampText(value, max) {
  const s = String(value ?? "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function oneLine(value, max) {
  return clampText(String(value ?? "").replace(/\s+/g, " "), max);
}

function shortId(id) {
  const s = String(id ?? "");
  return s.length > 10 ? s.slice(4, 12) : s || "session";
}

/** "workspaces/infrastructure/server-management" → { workspace, project } */
function parseSubpath(subpath) {
  if (typeof subpath !== "string" || !subpath) return {};
  const parts = subpath.split("/").filter(Boolean);
  const i = parts.indexOf("workspaces");
  if (i === -1) return { project: parts[parts.length - 1] };
  return { workspace: parts[i + 1], project: parts[i + 2] };
}

/** "/home/x/opencode-coder-env/workspaces/a/b" → "workspaces/a/b" */
export function subpathFromDirectory(dir) {
  const m = /\/workspaces\/(.+)$/.exec(String(dir ?? "").replace(/\/+$/, ""));
  return m ? `workspaces/${m[1]}` : "";
}

/** "/home/<owner>/opencode-coder-env/…" → owner */
function ownerFromDirectory(dir) {
  const m = /^\/home\/([^/]+)\//.exec(String(dir ?? ""));
  return m ? m[1] : "";
}

/** "https://coder.mueller-nas.de/" → "coder.mueller-nas.de" */
function hostFromUrl(value) {
  const s = String(value ?? "").trim();
  if (!s) return "";
  try {
    return new URL(s).host;
  } catch {
    return "";
  }
}

/** Kandidaten-Objekte eines Events — V2 liefert properties/data, wir sind tolerant. */
function candidates(event) {
  const out = [];
  for (const value of [event?.properties, event?.data, event?.payload, event]) {
    if (value && typeof value === "object") out.push(value);
  }
  return out;
}

function pick(event, key) {
  for (const candidate of candidates(event)) {
    const value = candidate[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

/** Session-Metadaten (id, title, subpath, location.directory, parentID). */
function sessionInfo(event) {
  const list = [
    event?.properties?.info,
    event?.properties?.session,
    event?.data?.info,
    event?.data?.session,
    event?.info,
    event?.session,
  ];
  for (const value of list) {
    if (value && typeof value === "object" && (value.id || value.sessionID)) return value;
  }
  return undefined;
}

/**
 * Sucht begrenzt rekursiv nach einer Session-ID. Nötig für Events ohne
 * data.sessionID/durable: `form.created` trägt sie z. B. in data.form.sessionID.
 */
function findSessionID(value, depth = 0) {
  if (depth > 4 || !value || typeof value !== "object") return undefined;
  for (const [key, child] of Object.entries(value)) {
    if (typeof child === "string") {
      if (!child) continue;
      if (key === "sessionID" || key === "sessionId") return child;
      if (/^ses_[A-Za-z0-9]+$/.test(child)) return child;
    } else if (child && typeof child === "object") {
      const found = findSessionID(child, depth + 1);
      if (found) return found;
    }
  }
  return undefined;
}

function sessionIDOf(event, info) {
  const direct = pick(event, "sessionID");
  if (typeof direct === "string" && direct) return direct;
  const aggregate = event?.durable?.aggregateID;
  if (typeof aggregate === "string" && aggregate) return aggregate;
  if (info && typeof info.id === "string" && info.id) return info.id;
  return findSessionID(event?.data) ?? findSessionID(event);
}

/** Text einer Message-Part — nur Assistenten-Text, nie der User-Prompt. */
function textFromEvent(event) {
  // V2: die vollständige Antwort steht in session.text.ended (je Text-Teil).
  if (event?.type === "session.text.ended") {
    const text = event?.data?.text;
    return typeof text === "string" ? text.trim() : "";
  }
  const part = pick(event, "part");
  if (part && typeof part.text === "string" && part.text.trim()) {
    if (part.type && part.type !== "text") return "";
    if (part.role && part.role !== "assistant") return "";
    return part.text.trim();
  }
  const info = pick(event, "info");
  if (info && typeof info.text === "string" && info.text.trim()) {
    if (info.role && info.role !== "assistant") return "";
    return info.text.trim();
  }
  return "";
}

/** Frage + Auswahloptionen als lesbarer Text (V2: Event `form.created`). */
function questionBody(event) {
  const data = pick(event, "data") ?? pick(event, "properties") ?? {};
  const collected = [];
  const add = (value) => {
    if (!value) return;
    if (Array.isArray(value)) value.forEach(add);
    else if (typeof value === "object") collected.push(value);
  };
  for (const source of [
    data.questions,
    data.question,
    data.fields,
    data.items,
    data.form?.questions,
    data.form?.fields,
    data.form?.items,
  ]) {
    add(source);
  }
  if (!collected.length && data.form && typeof data.form === "object") collected.push(data.form);

  const lines = [];
  for (const q of collected.slice(0, 3)) {
    const headline = oneLine(q?.title ?? q?.header ?? q?.label ?? q?.name ?? "", 120);
    const detail = oneLine(q?.question ?? q?.description ?? q?.text ?? q?.prompt ?? "", 200);
    const text = headline && detail ? `${headline}\n${detail}` : headline || detail;
    if (!text) continue;
    const options = Array.isArray(q?.options) ? q.options : Array.isArray(q?.choices) ? q.choices : [];
    // Eine Option pro Zeile, nummeriert — auf dem Handy war in der
    // Ein-Zeilen-Variante (" | ") nur die erste Option sichtbar.
    const rendered = options
      .map((o) => oneLine(o?.label ?? o?.title ?? o?.value ?? o?.id ?? o ?? "", MAX_OPTION_LEN))
      .filter(Boolean)
      .map((label, index) => `${index + 1}) ${label}`);
    lines.push(rendered.length ? `${text}\n${rendered.join("\n")}` : text);
  }
  if (!lines.length) {
    const single = oneLine(data.prompt ?? data.message ?? data.text ?? data.title ?? "", 160);
    if (single) lines.push(single);
  }
  return lines.join("\n\n");
}

function permissionBody(event) {
  const perm = pick(event, "permission") ?? pick(event, "info") ?? {};
  const what = oneLine(perm?.title ?? perm?.name ?? perm?.type ?? "", 100);
  const where = oneLine(
    perm?.metadata?.command ??
      perm?.metadata?.filePath ??
      perm?.metadata?.path ??
      perm?.pattern ??
      perm?.description ??
      "",
    160
  );
  // Optionen (falls die Freigabe welche mitbringt) wie bei Fragen: eine pro Zeile.
  const options = Array.isArray(perm?.options)
    ? perm.options
    : Array.isArray(perm?.choices)
      ? perm.choices
      : [];
  const rendered = options
    .map((o) => oneLine(o?.label ?? o?.title ?? o?.value ?? o?.id ?? o ?? "", MAX_OPTION_LEN))
    .filter(Boolean)
    .map((label, index) => `${index + 1}) ${label}`);
  const head = what && where ? `${what}: ${where}` : what || where;
  if (!rendered.length) return head;
  return head ? `${head}\n${rendered.join("\n")}` : rendered.join("\n");
}

function errorBody(event) {
  const err = pick(event, "error") ?? pick(event, "message");
  const text =
    typeof err === "string" ? err : err?.message ?? err?.data?.message ?? err?.name ?? "";
  return oneLine(text, 200);
}

function fallbackBody(event) {
  try {
    return oneLine(JSON.stringify(pick(event, "data") ?? pick(event, "properties") ?? event), 180);
  } catch {
    return "";
  }
}

// ── Gemeinsamer Zustand (ein Zustand je Prozess, mehrere Instanzen) ────────
//
// OpenCode lädt das Plugin einmal je Location (im Infrastructure-Container
// z. B. 6×: /, /root und je Workspace-Ordner). Jede Instanz abonniert den
// öffentlichen Event-Stream und sieht daher dieselben Events. Der Zustand
// hängt deshalb am Prozess (globalThis) und nicht am Modul: so erzeugt ein
// Event genau eine Nachricht, egal wie viele Instanzen es beobachten.

const STATE_KEY = Symbol.for("das.ntfy-notify.state");

function newState() {
  return {
    meta: new Map(), // sessionID → { title, subpath, directory, parentID }
    children: new Map(), // Kind-Session → Eltern-Session
    working: new Set(), // Sessions, die gerade arbeiten
    lastText: new Map(), // letzter Assistenten-Text je Session
    lastSent: new Map(), // Dedup: "sessionID:art" → Zeitstempel
  };
}

function sharedState() {
  if (!globalThis[STATE_KEY]) globalThis[STATE_KEY] = newState();
  return globalThis[STATE_KEY];
}

const SHARED = sharedState();

/** Nur für Tests: geteilten Zustand zurücksetzen. */
export function resetSharedState() {
  for (const map of Object.values(SHARED)) {
    if (map instanceof Map || map instanceof Set) map.clear();
  }
  const reply = replyRuntime();
  if (Array.isArray(reply.sentIds)) reply.sentIds.length = 0;
  else if (reply.sentIds instanceof Set) reply.sentIds.clear();
  if (Array.isArray(reply.processedIds)) reply.processedIds.length = 0;
  reply.lastSessionID = "";
  reply.replyBridgeStarted = false;
}

// ── Prozessglobaler Zustand der Antwort-Brücke ────────────────────────────
//
// Dieselbe Begründung wie beim Event-Zustand: mehrere Plugin-Instanzen (eine
// je Location) teilen sich einen Prozess, die Hintergrundschleife darf aber
// nur einmal laufen. sentIds/lastSessionID liegen deshalb ebenfalls am
// Prozess und werden vom Notifier (send/sendOnce) genauso benutzt wie von der
// Brücke selbst.

const REPLY_KEY = Symbol.for("ntfy.notify.runtime");

function newReplyState() {
  return {
    sentIds: [], // IDs der selbst gesendeten ntfy-Nachrichten
    processedIds: [], // IDs der bereits verarbeiteten Fremd-Nachrichten (Idempotenz)
    lastSessionID: "", // Session der zuletzt gesendeten Benachrichtigung
    replyBridgeStarted: false, // Schleife läuft bereits in diesem Prozess
  };
}

/** Zustand der Antwort-Brücke (ein Objekt je Prozess, tolerant nachgerüstet). */
export function replyRuntime() {
  const existing = globalThis[REPLY_KEY];
  if (!existing) {
    globalThis[REPLY_KEY] = newReplyState();
    return globalThis[REPLY_KEY];
  }
  if (!Array.isArray(existing.sentIds) && !(existing.sentIds instanceof Set)) {
    existing.sentIds = [];
  }
  if (!Array.isArray(existing.processedIds) && !(existing.processedIds instanceof Set)) {
    existing.processedIds = [];
  }
  if (typeof existing.lastSessionID !== "string") existing.lastSessionID = "";
  if (typeof existing.replyBridgeStarted !== "boolean") existing.replyBridgeStarted = false;
  return existing;
}

function hasSentId(runtime, id) {
  if (!id || !runtime?.sentIds) return false;
  const list = runtime.sentIds;
  if (typeof list.has === "function") return list.has(id);
  return Array.isArray(list) && list.includes(id);
}

/** Eigene Message-ID merken (begrenzt, damit der Zustand nicht wächst). */
function rememberSentId(id, runtime = replyRuntime()) {
  if (typeof id !== "string" || !id) return;
  if (!Array.isArray(runtime.sentIds) && !(runtime.sentIds instanceof Set)) {
    runtime.sentIds = [];
  }
  const list = runtime.sentIds;
  if (typeof list.add === "function") {
    list.add(id);
    while (list.size > MAX_SENT_IDS) list.delete(list.values().next().value);
    return;
  }
  if (list.includes(id)) return;
  list.push(id);
  while (list.length > MAX_SENT_IDS) list.shift();
}

function hasProcessedId(runtime, id) {
  if (!id) return false;
  const list = runtime?.processedIds;
  if (typeof list?.has === "function") return list.has(id);
  return Array.isArray(list) && list.includes(id);
}

/**
 * Fremde Message-ID als verarbeitet merken (begrenzt wie sentIds).
 *
 * Grund: ntfy löst `since=<messageID>` nur sekundengenau auf. Zwei Nachrichten
 * in derselben Sekunde (die Nutzerantwort und unsere eigene Antwort darauf)
 * bekommen denselben Zeitstempel; ein Poll mit `since` der ersten liefert sie
 * deshalb immer wieder aus. Der `since`-Zeiger allein kann das nicht lösen.
 */
function rememberProcessedId(id, runtime = replyRuntime()) {
  if (typeof id !== "string" || !id) return;
  if (!Array.isArray(runtime.processedIds) && !(runtime.processedIds instanceof Set)) {
    runtime.processedIds = [];
  }
  const list = runtime.processedIds;
  if (typeof list.add === "function") {
    list.add(id);
    while (list.size > MAX_PROCESSED_IDS) list.delete(list.values().next().value);
    return;
  }
  if (list.includes(id)) return;
  list.push(id);
  while (list.length > MAX_PROCESSED_IDS) list.shift();
}

function prune() {
  const limit = 300;
  for (const value of Object.values(SHARED)) {
    if (!(value instanceof Map) || value.size <= limit) continue;
    let excess = value.size - limit;
    for (const key of value.keys()) {
      value.delete(key);
      if (--excess <= 0) break;
    }
  }
}

// ── Kern: Event → ntfy-Nachricht ──────────────────────────────────────────

export function createNtfyNotifier(options = {}) {
  const env = options.env ?? process.env;
  const url = String(options.url ?? env.NTFY_URL ?? DEFAULT_URL).replace(/\/+$/, "");
  const token = String(options.token ?? env.NTFY_TOKEN ?? "");
  const topic = String(options.topic ?? env.NTFY_TOPIC ?? DEFAULT_TOPIC);
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const now = options.now ?? (() => Date.now());
  const logger = options.log ?? debug;
  const cooldownMs = Number(options.cooldownMs ?? COOLDOWN_MS);
  const contextDirectory = options.directory ?? "";
  const client = options.client;
  const state = options.state ?? SHARED;
  // Zustand der Antwort-Brücke: eigene Message-IDs + letzte Ziel-Session.
  const replyState = options.replyState ?? replyRuntime();

  function metaFor(sessionID) {
    return state.meta.get(sessionID) ?? {};
  }

  function projectFor(sessionID) {
    const parsed = parseSubpath(metaFor(sessionID).subpath);
    return parsed.project || parsed.workspace || "opencode";
  }

  function titleFor(sessionID) {
    return oneLine(metaFor(sessionID).title ?? "", 120) || `Session ${shortId(sessionID)}`;
  }

  function linkFor(sessionID) {
    // Escape-Hatch: eine fest konfigurierte URL gewinnt immer.
    const fixed = String(env.NTFY_SESSION_URL ?? "").trim();
    if (fixed) return fixed;

    const parsed = parseSubpath(metaFor(sessionID).subpath);
    const owner =
      ownerFromDirectory(metaFor(sessionID).directory) || ownerFromDirectory(contextDirectory);
    if (parsed.workspace && owner) {
      // Link auf die Port-App der laufenden `opencode serve`-Instanz (die
      // OpenCode-Web-UI). Muster wie VSCODE_PROXY_URI im Container:
      //   https://<port>--<agent>--<workspace>--<owner>-<coder-host>
      // Der Agent ist das Workspace-Agent-Segment (meist "main"), der Port
      // gehört zur Web-UI (49374). Der Domain-Teil wird aus CODER_AGENT_URL
      // abgeleitet, damit hier nichts hart kodiert ist. Die frühere App
      // `herdr` ist defekt (HTTP 500) und wird bewusst nicht mehr verwendet.
      const agent = env.CODER_WORKSPACE_AGENT_NAME || "main";
      const workspace = env.CODER_WORKSPACE_NAME || parsed.workspace;
      const user = env.CODER_WORKSPACE_OWNER_NAME || owner;
      const port = env.NTFY_SESSION_PORT || "49374";
      const coderHost = hostFromUrl(env.CODER_AGENT_URL) || "coder.mueller-nas.de";
      return `https://${port}--${agent}--${workspace}--${user}-${coderHost}`;
    }
    return String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/");
  }

  function absorb(info) {
    if (!info || typeof info !== "object" || typeof info.id !== "string" || !info.id) return;
    if (info.parentID) state.children.set(info.id, info.parentID);
    const entry = state.meta.get(info.id) ?? {};
    if (typeof info.title === "string" && info.title.trim()) entry.title = info.title.trim();
    if (typeof info.subpath === "string" && info.subpath) entry.subpath = info.subpath;
    const dir = info.location?.directory ?? info.location?.path ?? info.directory;
    if (typeof dir === "string" && dir) entry.directory = dir;
    if (!entry.subpath && entry.directory) entry.subpath = subpathFromDirectory(entry.directory);
    if (info.parentID) entry.parentID = info.parentID;
    state.meta.set(info.id, entry);
  }

  /** Projekt-Kontext aus dem Event übernehmen (V2: location.directory). */
  function absorbLocation(sessionID, directory) {
    if (typeof sessionID !== "string" || !sessionID) return;
    if (typeof directory !== "string" || !directory) return;
    const entry = state.meta.get(sessionID) ?? {};
    if (!entry.directory) entry.directory = directory;
    if (!entry.subpath) entry.subpath = subpathFromDirectory(directory);
    state.meta.set(sessionID, entry);
  }

  /** Metadaten bei Bedarf über den Server nachladen (Titel/Pfad fehlen oft). */
  async function ensureMeta(sessionID) {
    if (!sessionID) return;
    const entry = metaFor(sessionID);
    if (entry.title && entry.subpath) return;
    if (!client?.session?.get) return;
    try {
      absorb(await client.session.get({ sessionID }));
    } catch (error) {
      logger("session.get fehlgeschlagen:", error?.message ?? String(error));
    }
  }

  async function send(payload) {
    if (!url || !token) {
      logger("kein NTFY_TOKEN/NTFY_URL gesetzt – Nachricht verworfen");
      return false;
    }
    // Fragen/Freigaben dürfen länger sein (Optionsliste) — payload.maxBody gewinnt.
    const maxBody = Number(payload.maxBody) > 0 ? Number(payload.maxBody) : MAX_BODY;
    const body = {
      topic,
      message: clampText(payload.message, maxBody) || "…",
      title: oneLine(payload.title, MAX_TITLE),
      priority: payload.priority ?? 3,
      tags: payload.tags ?? ["speech_balloon"],
    };
    if (payload.click) {
      body.click = payload.click;
      body.actions = [{ action: "view", label: "Session öffnen", url: payload.click }];
    }
    const init = {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      init.signal = AbortSignal.timeout(8000);
    }
    try {
      const res = await fetchImpl(url, init);
      if (!res?.ok) {
        logger(`Publish fehlgeschlagen: HTTP ${res?.status ?? "?"}`);
        return false;
      }
      // Message-ID der eigenen Nachricht merken — daran erkennt die
      // Antwort-Brücke später, dass die Nachricht von uns selbst stammt.
      let id = "";
      try {
        const data = typeof res.json === "function" ? await res.json() : undefined;
        if (data && typeof data.id === "string") id = data.id;
      } catch {
        /* Antwort ohne JSON-Body – dann gibt es eben keine ID */
      }
      rememberSentId(id, replyState);
      return true;
    } catch (error) {
      logger("Publish-Fehler:", error?.message ?? String(error));
      return false;
    }
  }

  async function sendOnce(sessionID, kind, payload) {
    const key = `${sessionID ?? "?"}:${kind}`;
    const t = now();
    if (t - (state.lastSent.get(key) ?? 0) < cooldownMs) return false;
    state.lastSent.set(key, t);
    prune();
    const sent = await send(payload);
    // Ziel der Antwort-Brücke: die Session der zuletzt zugestellten Meldung.
    if (sent && typeof sessionID === "string" && sessionID) {
      replyState.lastSessionID = sessionID;
    }
    return sent;
  }

  /**
   * Subagent-Session? V2 liefert parentID ausschließlich über session.get,
   * nicht in den Events — deshalb hier einmalig nachladen (danach gecacht).
   */
  async function isSubagent(sessionID) {
    if (!sessionID) return false;
    if (state.children.has(sessionID)) return true;
    await ensureMeta(sessionID);
    return state.children.has(sessionID);
  }

  /**
   * Turn-Ende melden. Ausgelöst von session.execution.succeeded (Turn-Ende in
   * V2), session.step.ended mit finish=stop (Vorgänger-Event) oder
   * session.idle (dokumentiert, in 2.0.29 aber nicht ausgelöst) — der
   * Cooldown sorgt dafür, dass daraus nur eine Nachricht wird.
   */
  async function finishTurn(sessionID) {
    // Nur melden, wenn die Session wirklich gearbeitet hat — sonst würde jede
    // frisch angelegte/leere Session eine Meldung auslösen.
    if (!sessionID || !state.working.has(sessionID)) return;
    // Subagenten melden nicht; das tut die Eltern-Session am Ende ihres Turns.
    if (await isSubagent(sessionID)) {
      state.working.delete(sessionID);
      state.lastText.delete(sessionID);
      return;
    }
    state.working.delete(sessionID);
    const summary = clampText(state.lastText.get(sessionID) ?? "", 260);
    state.lastText.delete(sessionID);
    await ensureMeta(sessionID);
    await sendOnce(sessionID, "idle", {
      title: `✅ Fertig — ${projectFor(sessionID)}`,
      message: withHeader(sessionID, summary),
      priority: 3,
      tags: ["white_check_mark"],
      click: linkFor(sessionID),
    });
  }

  async function reportError(sessionID, event) {
    if (!sessionID) return;
    if (await isSubagent(sessionID)) return;
    state.working.add(sessionID);
    await ensureMeta(sessionID);
    await sendOnce(sessionID, "error", {
      title: `⚠️ Fehler — ${projectFor(sessionID)}`,
      message: withHeader(sessionID, errorBody(event) || fallbackBody(event)),
      priority: 5,
      tags: ["warning"],
      click: linkFor(sessionID),
    });
  }

  function withHeader(sessionID, body) {
    const title = titleFor(sessionID);
    return body ? `${title}\n\n${body}` : title;
  }

  function markWorking(sessionID) {
    if (typeof sessionID === "string" && sessionID && !state.children.has(sessionID)) {
      state.working.add(sessionID);
    }
  }

  async function handleEvent(event) {
    const type = event?.type;
    if (typeof type !== "string") return;

    const info = sessionInfo(event);
    if (info) absorb(info);
    const sessionID = sessionIDOf(event, info);

    // V2 liefert den Projekt-Kontext direkt mit: location.directory.
    absorbLocation(sessionID, event?.location?.directory);

    // Subagent-Sessions (parentID gesetzt) melden nichts — die Eltern-Session tut es.
    if (sessionID && state.children.has(sessionID)) return;

    // V2: vollständiger Antwort-Text je Text-Teil — der letzte gewinnt.
    const text = textFromEvent(event);
    if (sessionID && text) {
      state.lastText.set(sessionID, text);
      state.working.add(sessionID);
    }

    switch (type) {
      case "session.status": {
        const kind = pick(event, "status");
        const value = typeof kind === "string" ? kind : kind?.type;
        if (sessionID && typeof value === "string" && value.toLowerCase() !== "idle") {
          state.working.add(sessionID);
        }
        return;
      }
      // ── „Arbeitet gerade": V2-Eventnamen (+ V1-Altlasten, tolerant) ────
      case "session.execution.started":
      case "session.step.started":
      case "session.step.streamed":
      case "session.reasoning.started":
      case "session.reasoning.delta":
      case "session.text.started":
      case "session.text.delta":
      case "session.tool.input.started":
      case "session.tool.called":
      case "session.tool.progress":
      case "session.tool.success":
      case "session.tool.failed":
      case "session.shell.started":
      case "session.retry.scheduled":
      case "form.replied":
      case "permission.replied":
      case "message.part.updated":
      case "message.updated":
      case "tool.execute.before":
      case "tool.execute.after":
      case "session.compacted":
        if (sessionID) state.working.add(sessionID);
        return;
      // ── Turn-Ende ─────────────────────────────────────────────────────
      case "session.idle":
      case "session.execution.succeeded":
        await finishTurn(sessionID);
        return;
      case "session.step.ended": {
        // In 2.0.29 das verlässliche Ende eines Turns: der letzte Schritt
        // endet mit finish=stop. Zwischenschritte haben finish=tool-calls.
        const finish = pick(event, "finish");
        if (typeof finish === "string" && /^(stop|end_turn|end-turn|length)$/.test(finish)) {
          await finishTurn(sessionID);
        } else if (sessionID) {
          state.working.add(sessionID);
        }
        return;
      }
      // ── Fehler ────────────────────────────────────────────────────────
      case "session.error":
      case "session.execution.failed":
        await reportError(sessionID, event);
        return;
      case "session.step.failed": {
        // Ein fehlgeschlagener Schritt ist ein echter Fehler; ein einzelner
        // Tool-Fehler (session.tool.failed) ist meist nur ein Zwischenschritt.
        state.working.add(sessionID);
        await reportError(sessionID, event);
        return;
      }
      case "form.created":
      case "form.create":
      case "question.asked":
      case "question.requested": {
        // Subagenten fragen nicht über ntfy — das tut die Eltern-Session.
        if (!sessionID || (await isSubagent(sessionID))) return;
        markWorking(sessionID);
        await ensureMeta(sessionID);
        await sendOnce(sessionID, "question", {
          title: `❓ Frage — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, questionBody(event) || fallbackBody(event)),
          priority: 4,
          tags: ["question"],
          click: linkFor(sessionID),
          maxBody: MAX_QUESTION_BODY,
        });
        return;
      }
      case "permission.asked":
      case "permission.requested": {
        // Subagenten fordern keine Freigaben an, die der Nutzer sehen müsste.
        if (!sessionID || (await isSubagent(sessionID))) return;
        markWorking(sessionID);
        await ensureMeta(sessionID);
        await sendOnce(sessionID, "permission", {
          title: `🔐 Freigabe — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, permissionBody(event) || fallbackBody(event)),
          priority: 4,
          tags: ["lock"],
          click: linkFor(sessionID),
          maxBody: MAX_QUESTION_BODY,
        });
        return;
      }
      case "session.deleted": {
        if (sessionID) {
          state.meta.delete(sessionID);
          state.working.delete(sessionID);
          state.lastText.delete(sessionID);
        }
        return;
      }
      default:
        return;
    }
  }

  async function selfTest() {
    const host = process.env.HOSTNAME ?? "workspace";
    return send({
      title: `🔔 ntfy-Plugin aktiv (${host})`,
      message: `Selbsttest des OpenCode-V2-Plugins.\nURL: ${url}\nTopic: ${topic}`,
      priority: 2,
      tags: ["test_tube"],
      click: String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/"),
    });
  }

  return {
    handleEvent,
    markWorking,
    send,
    sendOnce,
    finishTurn,
    reportError,
    selfTest,
    absorb,
    absorbLocation,
    ensureMeta,
    projectFor,
    linkFor,
    _state: state,
  };
}

// ── Antwort-Brücke: ntfy-Nachricht → Prompt in die Session ────────────────
//
// Der Nutzer antwortet in der ntfy-App auf eine Meldung; die Antwort landet
// als ganz normale Nachricht im Topic. Diese Brücke holt neue Nachrichten im
// Hintergrund ab und speist sie als Prompt in die zuletzt benachrichtigte
// Session ein. Eigene Nachrichten (Message-ID aus sentIds ODER eigener
// Titel-Präfix) werden ignoriert — sonst würde das Plugin sich selbst
// beantworten. Bereits verarbeitete IDs werden gemerkt (processedIds), weil
// `since=<id>` sekundengenau aufgelöst wird und dieselbe Nachricht sonst mehr-
// fach im Poll auftaucht. Die reine Logik steckt in createReplyBridge() und ist damit
// ohne Netzwerk testbar; startReplyBridge() hängt nur die Schleife dran.

/** "0"/"false"/"no"/"off" schalten die Brücke ab (Default: an). */
function replyEnabled(env) {
  const value = String(env.NTFY_REPLY_ENABLED ?? "1")
    .trim()
    .toLowerCase();
  return !["0", "false", "no", "off"].includes(value);
}

/** NDJSON-Zeilen des /json-Endpunkts lesen; since zeigt danach auf die letzte ID. */
export function parseReplyStream(raw, since = "") {
  const messages = [];
  let nextSince = since;
  for (const line of String(raw ?? "").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let entry;
    try {
      entry = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (!entry || typeof entry !== "object") continue;
    // Auch open/keepalive-Zeilen tragen eine ID: sie rückt `since` weiter,
    // damit dieselbe Stelle nicht immer wieder abgefragt wird.
    if (typeof entry.id === "string" && entry.id) nextSince = entry.id;
    // keepalive/open-Zeilen tragen keine Nutzlast.
    if (entry.event && entry.event !== "message") continue;
    messages.push(entry);
  }
  return { messages, nextSince };
}

/** Wartet ms Millisekunden; bricht sofort ab, wenn das Signal abbricht. */
function sleep(ms, signal) {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    if (signal) signal.addEventListener("abort", done, { once: true });
  });
}

export function createReplyBridge(options = {}) {
  const env = options.env ?? process.env;
  const client = options.client ?? {};
  const runtime = options.runtime ?? replyRuntime();
  const log = options.log ?? (() => {});
  const publish = options.publish ?? (async () => false);
  const config = options.config ?? resolveConfig(env);
  const url = String(options.url ?? config.url ?? DEFAULT_URL).replace(/\/+$/, "");
  const token = String(options.token ?? config.token ?? "");
  const topic = String(options.topic ?? env.NTFY_REPLY_TOPIC ?? config.topic ?? DEFAULT_TOPIC);
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const pollSeconds = Number(options.pollSeconds ?? env.NTFY_REPLY_POLL_SECONDS);
  const pollMs = (pollSeconds > 0 ? pollSeconds : REPLY_POLL_SECONDS) * 1000;

  /** "" = fremde Nachricht, "id"/"title" = eigene Nachricht. */
  function ignoreReason(message) {
    const id = typeof message?.id === "string" ? message.id : "";
    if (id && hasSentId(runtime, id)) return "id";
    const title = String(message?.title ?? "");
    if (REPLY_PREFIXES.some((prefix) => title.startsWith(prefix))) return "title";
    return "";
  }

  function shouldIgnore(message) {
    return ignoreReason(message) !== "";
  }

  /**
   * Neue Nachrichten abholen (eine Runde, ohne Schleife). `since` bleibt
   * stehen, wenn die Antwort leer war — sonst würde dieselbe Nachricht bei
   * jeder Abfrage erneut verarbeitet.
   */
  async function pollOnce(since = "", signal) {
    if (!url || !token || typeof fetchImpl !== "function") {
      return { messages: [], nextSince: since };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), pollMs);
    const onAbort = () => controller.abort();
    if (signal) signal.addEventListener("abort", onAbort, { once: true });
    try {
      const res = await fetchImpl(
        `${url}/${encodeURIComponent(topic)}/json?poll=1&since=${encodeURIComponent(since)}`,
        { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal }
      );
      if (!res?.ok) throw new Error(`HTTP ${res?.status ?? "?"}`);
      const raw = typeof res.text === "function" ? await res.text() : "";
      return parseReplyStream(raw, since);
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
    }
  }

  async function hint(text) {
    await publish({
      title: REPLY_NO_SESSION_TITLE,
      message: text || "Es gibt gerade keine Session, an die ich die Antwort geben könnte.",
      priority: 2,
      tags: ["warning"],
    });
  }

  /**
   * Eine Nutzernachricht verarbeiten.
   * Rückgabe: { action: "duplicate" | "skipped" | "ignored" | "injected" | "no-session", … }
   */
  async function handleMessage(message) {
    // Zuerst die Idempotenz-Prüfung: ntfy löst `since=<id>` nur sekundengenau
    // auf, eine Nachricht aus derselben Sekunde kann also mehrfach im Poll
    // auftauchen. Ohne diese Sperre würde jede Wiederholung erneut eingespeist
    // (und jede Meldung ohne Ziel-Session erneut veröffentlicht).
    const messageId = typeof message?.id === "string" ? message.id : "";
    if (messageId && hasProcessedId(runtime, messageId)) {
      return { action: "duplicate", reason: "schon-verarbeitet" };
    }
    if (messageId) rememberProcessedId(messageId, runtime);

    const reason = ignoreReason(message);
    if (reason === "id") return { action: "skipped", reason: "eigene-id" };
    if (reason === "title") return { action: "ignored", reason: "eigener-titel" };

    const text = clampText(message?.message ?? message?.text ?? "", MAX_REPLY_TEXT);
    if (!text) return { action: "skipped", reason: "leer" };

    const sessionID = typeof runtime.lastSessionID === "string" ? runtime.lastSessionID : "";
    if (!sessionID || typeof client?.session?.prompt !== "function") {
      await hint(text);
      return { action: "no-session", text };
    }
    try {
      await client.session.prompt({ sessionID, text });
    } catch (error) {
      log("Antwort konnte nicht eingespeist werden:", error?.message ?? String(error));
      await hint(text);
      return { action: "no-session", text, reason: "prompt-fehler" };
    }
    // Bestätigung, damit der Nutzer weiß, dass die Antwort angekommen ist.
    await publish({
      title: REPLY_ACK_TITLE,
      message: clampText(text, 200),
      priority: 2,
      tags: ["leftwards_arrow_with_hook"],
    });
    return { action: "injected", sessionID, text };
  }

  /** Endlosschleife: abholen, verarbeiten, kurz warten. Wirft nie heraus. */
  async function loop(signal, startSince) {
    let since = String(startSince ?? Math.floor(Date.now() / 1000));
    while (!signal?.aborted) {
      try {
        const round = await pollOnce(since, signal);
        if (round.nextSince) since = round.nextSince;
        for (const message of round.messages) {
          if (signal?.aborted) break;
          try {
            await handleMessage(message);
          } catch (error) {
            log("Antwort konnte nicht verarbeitet werden:", error?.message ?? String(error));
          }
        }
      } catch (error) {
        if (signal?.aborted) break;
        log("Antwort-Abfrage fehlgeschlagen:", error?.message ?? String(error));
        await sleep(REPLY_ERROR_MS, signal);
        continue;
      }
      await sleep(REPLY_IDLE_MS, signal);
    }
  }

  return { shouldIgnore, ignoreReason, handleMessage, pollOnce, loop, url, topic };
}

/**
 * Startet die Antwort-Brücke genau einmal je Prozess.
 * Rückgabe: { bridge, stop } oder null (abgeschaltet/ohne Token/läuft schon).
 */
export function startReplyBridge(options = {}) {
  const env = options.env ?? process.env;
  const runtime = options.runtime ?? replyRuntime();
  const log = options.log ?? (() => {});

  if (!replyEnabled(env)) return null;
  if (runtime.replyBridgeStarted) return null;
  const config = options.config ?? resolveConfig(env);
  if (!config.token) return null;
  runtime.replyBridgeStarted = true;

  const bridge = createReplyBridge({
    ...options,
    client: options.client ?? options.ctx,
    env,
    runtime,
    log,
    config,
  });
  const controller = options.controller ?? new AbortController();
  const stop = () => controller.abort();
  void (async () => {
    try {
      await bridge.loop(controller.signal, options.since);
    } catch (error) {
      if (!controller.signal.aborted) {
        log("Antwort-Brücke beendet:", error?.message ?? String(error));
      }
    }
  })();
  return { bridge, controller, stop };
}

// ── V2-Plugin-Definition ───────────────────────────────────────────────────

/**
 * Baut die Plugin-Definition ({ id, setup }).
 * Als Fabrik exportiert, damit Tests Umgebung, argv und fetch vorgeben können.
 */
export function createPluginRuntime(options = {}) {
  const env = options.env ?? process.env;
  const argv = options.argv ?? process.argv.slice(2);
  const selftest = env.NTFY_SELFTEST === "1";

  return {
    id: PLUGIN_ID,
    async setup(ctx) {
      // Nur der Hintergrund-Service meldet; im Selbsttest auch ein Test-Server.
      if (!selftest && !isServiceProcess(argv)) return;

      const cfg = resolveConfig(env);
      const notifier = createNtfyNotifier({
        env: { ...env, NTFY_TOKEN: cfg.token, NTFY_URL: cfg.url, NTFY_TOPIC: cfg.topic },
        ...(options.fetch ? { fetch: options.fetch } : {}),
        ...(options.log ? { log: options.log } : {}),
        directory: typeof ctx?.location?.directory === "string" ? ctx.location.directory : "",
        client: ctx,
      });

      if (selftest) {
        if (!notifier) return;
        await notifier.selfTest();
        return;
      }

      if (!cfg.token) {
        console.error(
          `[ntfy-notify] NTFY_TOKEN fehlt (OpenBao secret/data/mcp/notify, Feld opencode_token, ` +
            `oder /etc/opencode.env) – Benachrichtigungen deaktiviert`
        );
        return;
      }

      console.log(
        `[ntfy-notify] aktiv (topic ${cfg.topic}, location ${ctx?.location?.directory ?? "?"})`
      );

      // "Arbeitet gerade": jeder abgeschickte Prompt markiert die Session.
      try {
        await ctx.session.hook("prompt", (event) => {
          notifier.markWorking(event?.sessionID ?? event?.session?.id);
        });
      } catch (error) {
        console.error("[ntfy-notify] prompt-Hook nicht registriert:", error?.message ?? error);
      }

      // Antwort-Brücke: Nutzerantworten aus dem Topic zurück in die Session.
      const replyBridge = startReplyBridge({
        ctx,
        env: { ...env, NTFY_TOKEN: cfg.token, NTFY_URL: cfg.url, NTFY_TOPIC: cfg.topic },
        publish: (payload) => notifier.send(payload),
        log: options.log ?? debug,
        ...(options.fetch ? { fetch: options.fetch } : {}),
      });
      if (replyBridge) {
        debug(`Antwort-Brücke aktiv (topic ${cfg.topic})`);
      }

      const controller = new AbortController();
      void (async () => {
        try {
          for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
            try {
              await notifier.handleEvent(event);
            } catch (error) {
              console.error("[ntfy-notify] Event-Fehler:", error?.message ?? error);
            }
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            console.error("[ntfy-notify] Event-Stream beendet:", error?.message ?? error);
          }
        }
      })();

      return () => {
        controller.abort();
        replyBridge?.stop();
      };
    },
  };
}

export default createPluginRuntime();
