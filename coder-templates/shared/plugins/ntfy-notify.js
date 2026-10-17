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
// melden. OpenCode V2 lädt Plugins pro HTTP-Span — es leben also mehrere
// Modul-Instanzen in einem Prozess. Der Zustand (working/Dedup) hängt deshalb
// an globalThis und wird über alle Instanzen geteilt, damit ein Event, das
// mehrere Instanzen sehen, nur eine Nachricht erzeugt. Der Ereignisstrom wird
// nach einem Ende oder Fehler automatisch neu aufgebaut (siehe
// createPluginRuntime.setup), damit ein Plugin-Neuladen die Meldungen nicht
// still abreißen lässt.
//
// ntfy ist hier ausschließlich ein Meldeweg: das Plugin veröffentlicht
// Meldungen und liest nichts aus dem Topic. Geantwortet wird in der Session
// selbst über den Knopf "Session öffnen" in der Meldung. Einen Weg zurück
// (Antwort aus der ntfy-App in eine Session einspeisen) gibt es bewusst nicht
// mehr: wer in ein Topic schreiben kann, hätte damit Prompts in Sessions mit
// MCP-Rechten einspeisen können — ein Topic ist kein vertrauenswürdiger Kanal
// (siehe docs/notifications.md, Tag ntfy-antwortweg-vor-rueckbau).
//
// Fehlermeldungen gehen kurz verzögert raus (ERROR_HOLD_MS, siehe
// reportError): bricht der Nutzer den Turn selbst ab
// (session.execution.interrupted) oder startet der Dienst neu
// (location.shutdown), wird die noch offene Meldung verworfen — sonst käme
// "⚠️ Fehler" auch für einen selbst abgebrochenen Turn. Fehlschläge derselben
// Session im Fenster werden zu einer Meldung zusammengefasst.
//
// Umgebungsvariablen:
//   NTFY_URL    Basis-URL        (Default https://ntfy.mueller-nas.de)
//   NTFY_TOKEN  Bearer-Token     (ohne Token bleibt das Plugin still)
//   NTFY_TOPIC  Topic            (Default "ai")
//   NTFY_TOPIC_PREFIX  Opt-in: Topic je Arbeitsbereich. Ist die Variable gesetzt
//                     und ist der Arbeitsbereich bekannt, gilt
//                     "<prefix>-<workspace>" (z. B. "oc" +
//                     "server-management" → "oc-server-management"). Ohne den
//                     Prefix bleibt es exakt bei NTFY_TOPIC; die Ableitung ist
//                     bewusst opt-in, sonst landeten Meldungen in Topics, die
//                     niemand abonniert hat.
//   NTFY_TOPIC_NAME   Arbeitsbereichs-Teil des Topics, wenn das Verzeichnis
//                     keinen hergibt: der Dienst läuft im Repo-Wurzelverzeichnis,
//                     dessen Pfad enthält kein "/workspaces/". Zusammen mit
//                     NTFY_TOPIC_PREFIX gilt "<prefix>-<name>" (z. B. "oc" +
//                     "infrastructure" → "oc-infrastructure"). Wirkt NUR auf das
//                     Topic: Icon (NTFY_ICON_BASE) und Titel-Kennung "[<name>] "
//                     bleiben am Sitzungs-Verzeichnis bzw. an NTFY_WORKSPACE.
//   NTFY_ICON_BASE    Opt-in: Symbol-URL je Meldung. Ist die Variable gesetzt und
//                     ist der Arbeitsbereich bekannt, trägt die Meldung das
//                     ntfy-Feld "icon" = "<basis>/<workspace>.png?v=<fassung>"
//                     (im JSON heißt das Feld `icon`, als Header `Icon` — die
//                     App zeigt das Bild auf Android/iOS als Benachrichtigungs-
//                     symbol). Die Versionskennung ICON_VERSION hängt hinten an
//                     der URL und umgeht so den Icon-Cache der Apps. Ohne die
//                     Basis entfällt das Feld (Verhalten unverändert).
//   NTFY_DEBUG=1      Debug-Ausgaben ins OpenCode-Log
//   NTFY_SELFTEST=1   sendet beim Laden genau eine Testnachricht (Diagnose),
//                     unabhängig vom Prozess-Check
//   NTFY_SESSION_PORT Port der laufenden `opencode serve`-Web-UI für den
//                     Klick-Link (Default 49374)
//   NTFY_SESSION_URL  feste Session-URL — schlägt die automatische Ableitung
//   NTFY_SERVER_KEY   Optionaler Override; sonst base64url des Ursprungs der
//                     Basis. Serverschlüssel der OpenCode-Oberfläche im
//                     Klick-Link (<basis>/server/<schlüssel>/session/<id>).
//   NTFY_FALLBACK_URL Rückfall-Adresse für Meldungen ohne Session (Default
//                     https://coder.mueller-nas.de/)
//   NTFY_IGNORE_TITLES kommagetrennte Teilzeichenketten (getrimmt, Groß-/
//                     Kleinschreibung egal): enthält der Titel einer Session
//                     einen davon, wird für sie gar nichts gemeldet — weder
//                     Frage noch Fertig noch Fehler. Leer/ungesetzt = aus.
//   NTFY_LINK_STYLE   "session" (Default) = Klickziel ist der Session-Deep-Link
//                     (<basis>/server/<schlüssel>/session/<id>); "base" = nur
//                     die Basis (Startseite der Oberfläche, Übergangslösung).
//   NTFY_REPLY_LOG    Pfad eines Diagnose-Logs (Default: aus). Dorthin
//                     schreibt das Plugin, was es tut: Zustandsfehler der
//                     Meldungen und der Aufbau des Ereignisstroms. Es ist ein
//                     reines Protokoll — gelesen wird nur diese Datei, nie ein
//                     Topic. Ohne gesetzte Variable entsteht keine Datei.
//   NTFY_WORKSPACE    Kurzer Name des Arbeitsbereichs (z. B. "server-management").
//                     Er steht als "[<name>] " vor jedem Meldungstitel, damit
//                     bei mehreren Arbeitsbereichen auf dem Handy sofort
//                     erkennbar ist, woher die Meldung kommt. Ist die Variable
//                     nicht gesetzt, wird der Name aus dem letzten Pfadstück
//                     des Sitzungs-Verzeichnisses abgeleitet; ist auch das nicht
//                     möglich, bleibt der Titel unverändert.
//
//   Alle NTFY_*-Einstellungen liest das Plugin zusätzlich aus der Env-Datei
//   /etc/opencode.env (Pfad über NTFY_ENV_FILE änderbar): die Service-Umgebung
//   kommt nicht in jeder Installation im Service-Prozess an. Vorrang hat immer
//   die Prozess-Umgebung, danach die Datei, dann die Defaults.
//
// Installation: coder-templates/shared/plugins/ntfy-notify.js
//               → ~/.config/opencode/plugins/ (dort automatisch geladen)

import { appendFileSync, readFileSync } from "node:fs";

// Fassung der Symbol-Bilder (NTFY_ICON_BASE). ntfy und die Apps halten ein
// einmal geladenes Icon lokal im Zwischenspeicher (laut ntfy-Doku bis zu 24
// Stunden, teils bis die Meldung gelöscht wird) — sie würden ein geändertes
// Bild also weiter aus dem Cache zeigen. Die Kennung "?v=<n>" an der URL
// umgeht den Cache und erzwingt einmalig ein Neuladen. Bei jeder inhaltlichen
// Änderung der PNGs (tools/make-ntfy-icons.py) hochzählen — 3 ist der
// ausgetauschte Symbolsatz.
const ICON_VERSION = 3;

const DEFAULT_URL = "https://ntfy.mueller-nas.de";
const DEFAULT_TOPIC = "ai";
// Rückfall-Serverschlüssel im Klick-Link (/server/<schlüssel>/session/<id>),
// wenn sich der Ursprung der Basis nicht als URL auswerten lässt; sonst zählt
// base64url des Ursprungs der Basis (siehe serverKeyFromBase).
const DEFAULT_SERVER_KEY = "local";
const MAX_TITLE = 90;
const MAX_BODY = 500;
// Frage- und Freigabe-Meldungen tragen eine mehrzeilige Optionsliste — ntfy
// erlaubt bis 4096 Byte je Message, hier reichen 1500 Zeichen mit Puffer.
const MAX_QUESTION_BODY = 1500;
// Lange Optionstexte je Option kürzen, damit die Liste lesbar bleibt.
const MAX_OPTION_LEN = 120;
const COOLDOWN_MS = 15000;
const PLUGIN_ID = "das.ntfy-notify";
// Fehlermeldungen gehen kurz verzoegert raus (siehe reportError): bricht der
// Nutzer den Turn selbst ab (session.execution.interrupted) oder startet der
// Dienst neu (location.shutdown), trifft das noch im Fenster ein und die
// Meldung wird verworfen. Mehrere Fehlschlaege derselben Session im Fenster
// werden zu einer Meldung zusammengefasst. options.errorHoldMs uebersteuert
// die Wartezeit nur in Tests.
const ERROR_HOLD_MS = 3000;
// Ereignisstrom: endet er (z. B. Plugin-Neuladen) oder wirft er, wird er nach
// einer kurzen Wartezeit neu aufgebaut; bei wiederholtem Sofort-Ende wächst die
// Wartezeit bis EVENT_RETRY_MAX_MS. options.eventRetryMs übersteuert die
// Startwartezeit nur in Tests.
const EVENT_RETRY_MS = 1000;
const EVENT_RETRY_MAX_MS = 5000;

// ── Hilfsfunktionen ────────────────────────────────────────────────────────

/**
 * Die Workspace-Env-Datei einlesen (Schlüssel → Wert).
 *
 * `startup.sh` schreibt dort ALLE `NTFY_*`-Einstellungen nach
 * `/etc/opencode.env` (Mode 0600) — Token/URL/Topic, aber auch den
 * Session-Link (`NTFY_SESSION_URL`), den Serverschlüssel (`NTFY_SERVER_KEY`)
 * und die Kennungen (`NTFY_WORKSPACE`, `NTFY_ICON_BASE` …). Die
 * Service-Umgebung über `opencode service set env` kommt nicht in jeder
 * Installation im Service-Prozess an. Beim Zusammenführen haben Env-Variablen
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

/**
 * Diagnose-Zeile anhängen — nur wenn NTFY_REPLY_LOG gesetzt ist (der Name
 * bleibt aus Kompatibilität: das Log hat sich als reine Diagnose-Quelle
 * bewährt). Wirft nie.
 */
export function makeDiag(env) {
  const path = String(env.NTFY_REPLY_LOG ?? "").trim();
  return (event, detail = "") => {
    if (!path) return;
    try {
      appendFileSync(path, `${new Date().toISOString()} ${event}${detail ? `: ${detail}` : ""}\n`);
    } catch {
      /* Diagnose darf die Meldung nie stören */
    }
  };
}


/**
 * Klickziel-Stil: "base" setzt das Klickziel auf die reine Basis (Startseite
 * der Oberfläche) — Übergangslösung, bis der Session-Deep-Link überall trägt.
 * Alles andere (und der Default) bleibt beim bisherigen Verhalten "session".
 */
function linkStyle(env) {
  const value = String(env.NTFY_LINK_STYLE ?? "")
    .trim()
    .toLowerCase();
  return value === "base" ? "base" : "session";
}

/**
 * NTFY_IGNORE_TITLES: kommagetrennte Teilzeichenketten, getrimmt und in
 * Kleinschreibung. Leer/nicht gesetzt ergibt eine leere Liste (= aus).
 */
function ignoredTitleParts(env) {
  return String(env.NTFY_IGNORE_TITLES ?? "")
    .split(",")
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean);
}

/** Enthält der Session-Titel einen der Teile? (leerer Titel: nein) */
function titleMuted(parts, title) {
  const needle = String(title ?? "")
    .trim()
    .toLowerCase();
  if (!needle) return false;
  return parts.some((part) => needle.includes(part));
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

/**
 * Letztes Pfadstück eines Verzeichnisses — daraus entsteht der kurze Name des
 * Arbeitsbereichs im Titel: "/…/workspaces/infrastructure/server-management"
 * → "server-management". Leere Pfade ergeben "".
 */
function lastPathSegment(dir) {
  const parts = String(dir ?? "")
    .split(/[\\/]+/)
    .filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

/**
 * Kurzer Name des Arbeitsbereichs: NTFY_WORKSPACE gewinnt (die Env-Datei zählt
 * mit, siehe createPluginRuntime); sonst das letzte Pfadstück des Verzeichnisses
 * (/…/workspaces/infrastructure/server-management → server-management). Ist
 * beides nicht zu ermitteln, bleibt es bei "" (wie beim Titel). Klammern fliegen
 * raus, damit die Kennung eindeutig bleibt.
 */
export function workspaceNameFrom(env = {}, dir = "") {
  const clean = (value) =>
    String(value ?? "")
      .replace(/[[\]]/g, "")
      .trim();
  return clean(env.NTFY_WORKSPACE) || clean(lastPathSegment(dir));
}

/**
 * Arbeitsbereich für Topic und Symbol — strenger als workspaceNameFrom: es
 * zählen nur echte Workspace-Pfade (…/workspaces/<bereich>/<name> → <name>).
 * Das Kontext-Verzeichnis einer Plugin-Instanz kann auch "/", "/root" oder das
 * Repo-Wurzelverzeichnis sein; daraus entstünde sonst ein Topic wie
 * "oc-opencode-coder-env". NTFY_WORKSPACE gilt unabhängig davon.
 */
export function topicWorkspaceFrom(env = {}, dir = "") {
  const clean = (value) =>
    String(value ?? "")
      .replace(/[[\]]/g, "")
      .trim();
  const fromEnv = clean(env.NTFY_WORKSPACE);
  if (fromEnv) return fromEnv;
  const path = String(dir ?? "");
  if (!subpathFromDirectory(path)) return "";
  return clean(lastPathSegment(path));
}

/**
 * Arbeitsbereichs-Teil des TOPICS, wenn das Verzeichnis keinen hergibt:
 * NTFY_TOPIC_NAME gewinnt, sonst der übergebene Arbeitsbereich. Gedacht für
 * den Dienst im Repo-Wurzelverzeichnis (kein "/workspaces/" im Pfad). Bewusst
 * NICHT für Icon und Titel verwenden — die hängen weiter am Sitzungs-
 * Verzeichnis bzw. an NTFY_WORKSPACE (siehe workspaceForIcon/workspaceName).
 */
export function topicNameFrom(env = {}, workspace = "") {
  const clean = (value) =>
    String(value ?? "")
      .replace(/[[\]]/g, "")
      .trim();
  return clean(env.NTFY_TOPIC_NAME) || clean(workspace);
}

/**
 * Topic mit Arbeitsbereich (NTFY_TOPIC_PREFIX). Die Ableitung ist opt-in: ohne
 * Prefix oder ohne bekannten Arbeitsbereich kommt unverändert `base` zurück —
 * sonst landeten Meldungen in Topics, die der Nutzer nicht abonniert hat. Mit
 * Prefix und bekanntem Arbeitsbereich gilt "<prefix>-<workspace>", z. B. "oc" +
 * "server-management" → "oc-server-management". Abschließende Bindestriche im
 * Prefix werden entfernt, damit daraus kein "--" wird.
 */
export function topicWithWorkspace(base, env = {}, workspace = "") {
  const prefix = String(env.NTFY_TOPIC_PREFIX ?? "")
    .trim()
    .replace(/-+$/, "");
  const name = String(workspace ?? "").trim();
  if (!prefix || !name) return base;
  return `${prefix}-${name}`;
}

/**
 * Das endgültige Topic einer Instanz. Der Arbeitsbereich kommt aus
 * NTFY_TOPIC_NAME, sonst aus NTFY_WORKSPACE/dem Verzeichnis (siehe
 * topicNameFrom/topicWorkspaceFrom); zusammengesetzt wird er nur mit
 * NTFY_TOPIC_PREFIX. Icon und Titel bleiben davon unberührt — sie folgen
 * weiter dem Sitzungs-Verzeichnis.
 */
export function topicForEnv(base, env = {}, dir = "") {
  return topicWithWorkspace(base, env, topicNameFrom(env, topicWorkspaceFrom(env, dir)));
}

/**
 * Symbol-URL einer Meldung: NTFY_ICON_BASE +
 * "/<workspace>.png?v=<ICON_VERSION>". Ohne Basis oder ohne bekannten
 * Arbeitsbereich kommt "" zurück — dann setzt send() kein icon-Feld und das
 * Verhalten bleibt wie bisher. Ein abschließender Slash der Basis wird entfernt,
 * damit keine doppelten "//" entstehen. Die Versionskennung sitzt am Bildnamen
 * und umgeht so den lokalen Zwischenspeicher der Apps (siehe ICON_VERSION).
 */
export function iconUrlFor(env = {}, workspace = "") {
  const base = String(env.NTFY_ICON_BASE ?? "")
    .trim()
    .replace(/\/+$/, "");
  const name = String(workspace ?? "").trim();
  if (!base || !name) return "";
  return `${base}/${name}.png?v=${ICON_VERSION}`;
}

/**
 * Titel mit Kennung des Arbeitsbereichs: "[<name>] <Titel>". Der Titel bleibt
 * so vollständig wie möglich; reicht MAX_TITLE nicht, wird zuerst der Name
 * gekürzt (er trägt die kürzere Information). Ohne bekannten Namen bleibt der
 * Titel unverändert.
 */
function titleWithWorkspace(title, name) {
  const tag = String(name ?? "").trim();
  const text = oneLine(title, MAX_TITLE);
  if (!tag) return text;
  // "[" + "]" + Leerzeichen belegen drei Zeichen Rahmen; der Name bekommt den
  // Rest. Reicht der Platz nicht für den ganzen Namen, wird er gekürzt.
  const room = MAX_TITLE - text.length - 3;
  if (room >= tag.length) return `[${tag}] ${text}`;
  // Zu langer Titel: der Titel selbst wird am Ende gekürzt (send() begrenzt auf
  // MAX_TITLE) — die Kennung bleibt in jedem Fall vorn stehen.
  return oneLine(`[${tag.slice(0, Math.max(3, room - 1))}…] ${text}`, MAX_TITLE);
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

/**
 * Serverschlüssel der OpenCode-Oberfläche für eine Basis: NTFY_SERVER_KEY
 * gewinnt, wenn gesetzt und nicht leer (ausdrücklicher Override). Sonst zählt
 * base64url des Ursprungs der Basis — mit genau diesem Wert adressiert die
 * Oberfläche ihren Dienst (/server/<schlüssel>/session/<id>). Die frühere
 * Annahme "local" ließ die Oberfläche abstürzen, weil der Schlüssel dort ein
 * base64url-Wert ist. Lässt sich die Basis nicht als URL auswerten (oder hat
 * sie keinen echten Ursprung), bleibt es beim bisherigen Rückfall; die
 * Funktion wirft nie, die Meldung darf daran nicht scheitern.
 */
function serverKeyFromBase(base, env) {
  const configured = String(env.NTFY_SERVER_KEY ?? "").trim();
  if (configured) return configured;
  try {
    const origin = new URL(String(base ?? "")).origin;
    if (!origin || origin === "null") return DEFAULT_SERVER_KEY;
    return Buffer.from(origin).toString("base64url");
  } catch {
    return DEFAULT_SERVER_KEY;
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
  const questions = collected.slice(0, 3);
  const total = questions.length;
  questions.forEach((q, position) => {
    const headline = oneLine(q?.title ?? q?.header ?? q?.label ?? q?.name ?? "", 120);
    const detail = oneLine(q?.question ?? q?.description ?? q?.text ?? q?.prompt ?? "", 200);
    const text = headline && detail ? `${headline}\n${detail}` : headline || detail;
    if (!text) return;
    const options = Array.isArray(q?.options) ? q.options : Array.isArray(q?.choices) ? q.choices : [];
    // Eine Option pro Zeile, nummeriert — auf dem Handy war in der
    // Ein-Zeilen-Variante (" | ") nur die erste Option sichtbar.
    const rendered = options
      .map((o) => oneLine(o?.label ?? o?.title ?? o?.value ?? o?.id ?? o ?? "", MAX_OPTION_LEN))
      .filter(Boolean)
      .map((label, index) => `${index + 1}) ${label}`);
    // Kopfzeile "Frage i von n": bei mehreren Fragen einer Meldung ist sofort
    // klar, auf welche Frage sich die Optionen beziehen; die Nummerierung der
    // Optionen beginnt bei jeder Frage wieder bei "1)".
    const body = rendered.length ? `${text}\n${rendered.join("\n")}` : text;
    lines.push(`Frage ${position + 1} von ${total}\n${body}`);
  });
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
// OpenCode V2 lädt das Plugin pro HTTP-Span (Log: mehrere "loading plugin …
// http.span=…"-Zeilen), zusätzlich je Location (im Infrastructure-Container
// z. B. /, /root und je Workspace-Ordner). Jede Modul-Instanz hat dabei einen
// eigenen Scope, jede Instanz abonniert den öffentlichen Event-Stream und
// sieht daher dieselben Events. Der Zustand hängt deshalb an globalThis und
// nicht am Modul-Scope: so erzeugt ein Event genau eine Nachricht, egal wie
// viele Instanzen es beobachten.

function newState() {
  return {
    meta: new Map(), // sessionID → { title, subpath, directory, parentID }
    children: new Map(), // Kind-Session → Eltern-Session
    working: new Set(), // Sessions, die gerade arbeiten
    lastText: new Map(), // letzter Assistenten-Text je Session
    lastSent: new Map(), // Dedup: "sessionID:art" → Zeitstempel
  };
}

/** Prozessweit geteilter Zustand — in jeder Modul-Instanz dasselbe Objekt. */
export const SHARED = (globalThis.__ntfyNotifyShared ??= newState());

// Fehlende Felder defensiv nachfüllen: liegt im globalThis ein älteres oder
// teilweise gefülltes Objekt, fehlt sonst z. B. lastSent — und die Dedup liefe
// über Instanzen hinweg ins Leere.
for (const [key, value] of Object.entries(newState())) {
  if (SHARED[key] === undefined) SHARED[key] = value;
}

/**
 * Nur für Tests: den geteilten Zustand leeren (Dedup, Metadaten, „arbeitet
 * gerade"). Es gibt keinen zweiten Prozesszustand mehr — der Zustand der
 * Antwort-Brücke und die Zustandsdatei sind entfallen (ntfy ist nur noch
 * Meldeweg, siehe Dateikopf).
 */
export function resetSharedState() {
  for (const map of Object.values(SHARED)) {
    if (map instanceof Map || map instanceof Set) map.clear();
  }
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
  // Arbeitsbereich dieser Instanz: NTFY_WORKSPACE oder der Workspace-Pfad des
  // Kontext-Verzeichnisses. Er bestimmt Icon und Titel-Kennung — NTFY_TOPIC_NAME
  // gilt hier bewusst nicht (siehe workspaceForIcon/workspaceName).
  const contextWorkspace = topicWorkspaceFrom(env, options.directory ?? "");
  // Topic: NTFY_TOPIC plus Prefix/Arbeitsbereich (NTFY_TOPIC_PREFIX,
  // NTFY_TOPIC_NAME) — options.topic bleibt der ausdrückliche Override.
  const topic = String(
    options.topic ??
      topicForEnv(String(env.NTFY_TOPIC ?? DEFAULT_TOPIC), env, options.directory ?? "")
  );
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const now = options.now ?? (() => Date.now());
  const logger = options.log ?? debug;
  const cooldownMs = Number(options.cooldownMs ?? COOLDOWN_MS);
  const contextDirectory = options.directory ?? "";
  const client = options.client;
  const state = options.state ?? SHARED;
  // Stummschaltung per Titel (NTFY_IGNORE_TITLES) und Klickziel-Stil
  // (NTFY_LINK_STYLE) einmal auswerten — die Env ändert sich im Betrieb nicht.
  const mutedTitles = ignoredTitleParts(env);
  const baseOnlyLinks = linkStyle(env) === "base";
  const diag = options.diag ?? makeDiag(env);
  // Wartezeit, in der eine Fehlermeldung zurückgehalten wird (siehe
  // reportError). options.errorHoldMs übersteuert sie — nur in Tests.
  const errorHoldMs = (() => {
    const value = Number(options.errorHoldMs);
    return Number.isFinite(value) && value >= 0 ? value : ERROR_HOLD_MS;
  })();
  // Noch nicht verschickte Fehlermeldungen dieser Instanz: Session →
  // { timer, event, count }. Ein Abbruch oder Dienst-Neustart verwirft den
  // Eintrag; weitere Fehlschläge derselben Session zählen nur hoch.
  const pendingErrors = new Map();

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

  /** Feste Session-Basis (NTFY_SESSION_URL) — "" solange sie nicht gesetzt ist. */
  function fixedSessionUrl() {
    return String(env.NTFY_SESSION_URL ?? "").trim();
  }

  /**
   * Basis aus den Sitzungs-Metadaten: die Port-App der laufenden
   * `opencode serve`-Instanz (die OpenCode-Web-UI). Muster wie
   * VSCODE_PROXY_URI im Container:
   *   https://<port>--<agent>--<workspace>--<owner>-<coder-host>
   * Der Agent ist das Workspace-Agent-Segment (meist "main"), der Port gehört
   * zur Web-UI (49374). Der Domain-Teil wird aus CODER_AGENT_URL abgeleitet,
   * damit hier nichts hart kodiert ist. Die frühere App `herdr` ist defekt
   * (HTTP 500) und wird bewusst nicht mehr verwendet. Ohne Workspace-Pfad und
   * Inhaber lässt sich nichts ableiten — dann kommt "" zurück.
   */
  function derivedBase(sessionID) {
    const parsed = parseSubpath(metaFor(sessionID).subpath);
    const owner =
      ownerFromDirectory(metaFor(sessionID).directory) || ownerFromDirectory(contextDirectory);
    if (!parsed.workspace || !owner) return "";
    const agent = env.CODER_WORKSPACE_AGENT_NAME || "main";
    const workspace = env.CODER_WORKSPACE_NAME || parsed.workspace;
    const user = env.CODER_WORKSPACE_OWNER_NAME || owner;
    const port = env.NTFY_SESSION_PORT || "49374";
    const coderHost = hostFromUrl(env.CODER_AGENT_URL) || "coder.mueller-nas.de";
    return `https://${port}--${agent}--${workspace}--${user}-${coderHost}`;
  }

  /**
   * Kurzer Name des Arbeitsbereichs für die Titel-Kennung ("[<name>] …"):
   * NTFY_WORKSPACE gewinnt (die Env-Datei zählt mit, siehe
   * createPluginRuntime); sonst das letzte Pfadstück des Sitzungs-Verzeichnisses
   * (/…/workspaces/infrastructure/server-management → server-management). Ist
   * beides nicht zu ermitteln, bleibt der Titel unverändert ("") — siehe
   * workspaceNameFrom.
   */
  function workspaceName(sessionID) {
    return workspaceNameFrom(env, metaFor(sessionID).directory);
  }

  /**
   * Arbeitsbereich für Symbol UND Titel-Kennung: die Sitzung gewinnt (ihr
   * Verzeichnis ist der echte Workspace-Pfad), sonst der Arbeitsbereich der
   * Instanz. NTFY_TOPIC_NAME gilt hier ausdrücklich NICHT — die Variable setzt
   * nur den Topic-Namen. Ohne Arbeitsbereich gibt es kein icon-Feld.
   */
  function workspaceForIcon(sessionID) {
    return topicWorkspaceFrom(env, metaFor(sessionID).directory) || contextWorkspace;
  }

  /**
   * Basis der Session-Links — linkFor baut darauf den Session-Pfad auf.
   * NTFY_SESSION_URL gewinnt immer, sonst wird die Port-App der laufenden
   * `opencode serve`-Instanz aus den Sitzungs-Metadaten abgeleitet. Ohne
   * Session und ohne ableitbare Basis gilt NTFY_FALLBACK_URL.
   */
  function linkBase(sessionID) {
    // Escape-Hatch: eine fest konfigurierte URL gewinnt immer.
    const fixed = fixedSessionUrl();
    if (fixed) return fixed;

    const derived = derivedBase(sessionID);
    if (derived) return derived;

    return String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/");
  }

  /**
   * Klick-Ziel jeder Meldung: Session-Deep-Link der OpenCode-Oberfläche
   * (`/server/<serverKey>/session/<id>`, Solid-Router mit Path-Routing). Der
   * Serverschlüssel ist NTFY_SERVER_KEY, wenn gesetzt — sonst base64url des
   * Ursprungs der Basis (genau der Wert, den die Oberfläche verwendet; siehe
   * serverKeyFromBase). Die Session-ID wird URL-sicher eingesetzt. Ohne
   * Session-ID bleibt es bei der Basis. NTFY_LINK_STYLE=base setzt das
   * Klickziel auf die Basis (Übergangslösung, siehe linkStyle).
   */
  function linkFor(sessionID) {
    const base = linkBase(sessionID);
    // NTFY_LINK_STYLE=base: Klickziel ist nur die Startseite der Oberfläche.
    if (baseOnlyLinks) return base;
    const id = String(sessionID ?? "").trim();
    if (!id) return base;
    const serverKey = serverKeyFromBase(base, env);
    const path = `/server/${encodeURIComponent(serverKey)}/session/${encodeURIComponent(id)}`;
    return `${base.replace(/\/+$/, "")}${path}`;
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

  async function send(payload, sessionID) {
    if (!url || !token) {
      logger("kein NTFY_TOKEN/NTFY_URL gesetzt – Nachricht verworfen");
      return false;
    }
    // Fragen/Freigaben dürfen länger sein (Optionsliste) — payload.maxBody gewinnt.
    const maxBody = Number(payload.maxBody) > 0 ? Number(payload.maxBody) : MAX_BODY;
    const body = {
      topic,
      message: clampText(payload.message, maxBody) || "…",
      // Kennung des Arbeitsbereichs im Titel ("[<name>] …"): bei mehreren
      // Arbeitsbereichen ist auf dem Handy sofort sichtbar, woher die Meldung
      // kommt. Klickziel und Aktionen bleiben davon unberührt.
      title: oneLine(titleWithWorkspace(payload.title, workspaceName(sessionID)), MAX_TITLE),
      priority: payload.priority ?? 3,
      tags: payload.tags ?? ["speech_balloon"],
    };
    // Symbol der Meldung: ntfy-Feld `icon` (als Header `Icon`), die URL zeigt auf
    // das PNG des Arbeitsbereichs (NTFY_ICON_BASE, z. B. ein rohes
    // GitHub-Verzeichnis). Android/iOS zeigen das Bild als
    // Benachrichtigungssymbol. Ohne Basis oder ohne bekannten Arbeitsbereich
    // entfällt das Feld — dann ist das Verhalten unverändert.
    const icon = iconUrlFor(env, workspaceForIcon(sessionID));
    if (icon) body.icon = icon;
    // Aktionen aufbauen (ntfy erlaubt höchstens drei je Nachricht): vorhandene
    // übernehmen, dann das Klick-Ziel. Das Klick-Ziel selbst bleibt unverändert.
    const actions = Array.isArray(payload.actions)
      ? payload.actions.filter((action) => action && typeof action === "object")
      : [];
    if (payload.click) {
      body.click = payload.click;
      if (!actions.some((action) => action.action === "view" && action.url === payload.click)) {
        // Im Basis-Stil führt der Knopf nur auf die Startseite — dann heißt er auch so.
        actions.push({
          action: "view",
          label: baseOnlyLinks ? "OpenCode öffnen" : "Session öffnen",
          url: payload.click,
        });
      }
    }
    if (actions.length) body.actions = actions;
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
      // Die Meldung ist raus. Der Antwort-Body wird nicht mehr ausgewertet: die
      // Message-ID diente allein dem Antwortweg (Selbsterkennung im Topic).
      return true;
    } catch (error) {
      logger("Publish-Fehler:", error?.message ?? String(error));
      return false;
    }
  }

  /** Stummgeschaltete Session? (Titel enthält einen Teil aus NTFY_IGNORE_TITLES) */
  function isMuted(sessionID) {
    return titleMuted(mutedTitles, metaFor(sessionID).title);
  }

  async function sendOnce(sessionID, kind, payload) {
    // NTFY_IGNORE_TITLES: für diese Session geht gar nichts raus — weder Frage
    // noch Fertig noch Fehler. Der Cooldown wird dabei nicht angefasst.
    if (sessionID && isMuted(sessionID)) return false;
    const key = `${sessionID ?? "?"}:${kind}`;
    const t = now();
    if (t - (state.lastSent.get(key) ?? 0) < cooldownMs) return false;
    state.lastSent.set(key, t);
    prune();
    return send(payload, sessionID);
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

  /**
   * Trägt das Ereignis erkennbar einen Abbruch? `session.execution.interrupted`
   * ist ein eigenes Ereignis und meldet nichts (siehe handleEvent); manche
   * Fehler-Ereignisse tragen den Abbruch aber als Feld mit (aborted, abortedMs,
   * reason: "aborted", status: "cancelled" …). Auch dann darf keine Meldung
   * entstehen — sonst stünde „⚠️ Fehler" für einen selbst abgebrochenen Turn im
   * Topic.
   */
  function isAbortEvent(event) {
    const marker = /^(aborted|abort|interrupted|cancelled|canceled)$/i;
    const key = /^(aborted|abort|abortreason|reason|status|state|finish)$/i;
    const seen = new Set();
    const walk = (value, depth) => {
      if (depth > 3 || !value || typeof value !== "object" || seen.has(value)) return false;
      seen.add(value);
      for (const [name, child] of Object.entries(value)) {
        if (typeof child === "boolean") {
          if (child && /^(aborted|interrupted|cancelled|canceled)$/i.test(name)) return true;
          continue;
        }
        if (typeof child === "string") {
          if (key.test(name) && marker.test(child.trim())) return true;
          continue;
        }
        if (child && typeof child === "object" && walk(child, depth + 1)) return true;
      }
      return false;
    };
    return walk(event, 0);
  }

  /** Eine zurückgehaltene Fehlermeldung fallenlassen (Abbruch). */
  function cancelPendingError(sessionID) {
    if (!sessionID) return;
    const pending = pendingErrors.get(sessionID);
    if (!pending) return;
    clearTimeout(pending.timer);
    pendingErrors.delete(sessionID);
    // Diagnose: ohne diese Zeile wäre im Log nicht zu sehen, dass eine
    // Fehlermeldung wegen eines Abbruchs bewusst nicht verschickt wurde.
    diag("fehler-verworfen", `abbruch (${pending.count} Fehlschlag/Fehlschlaege)`);
  }

  /** Alle zurückgehaltenen Fehlermeldungen fallenlassen (Dienst-Neustart). */
  function cancelAllPendingErrors() {
    const count = pendingErrors.size;
    for (const pending of pendingErrors.values()) clearTimeout(pending.timer);
    pendingErrors.clear();
    if (count) diag("fehler-verworfen", "dienst-neustart");
  }

  /** Die zurückgehaltene Fehlermeldung jetzt verschicken. */
  async function flushError(sessionID, pending) {
    if (await isSubagent(sessionID)) return;
    state.working.add(sessionID);
    await ensureMeta(sessionID);
    const body = errorBody(pending.event) || fallbackBody(pending.event);
    // Mehrere Fehlschläge im Fenster ergeben eine Meldung — die Anzahl wird
    // genannt, damit der Zusammenhang nicht verloren geht.
    const hint = pending.count > 1 ? `\n\n(${pending.count} Fehlschläge zusammengefasst)` : "";
    await sendOnce(sessionID, "error", {
      title: `⚠️ Fehler — ${projectFor(sessionID)}`,
      message: withHeader(sessionID, `${body}${hint}`),
      priority: 5,
      tags: ["warning"],
      click: linkFor(sessionID),
    });
  }

  /**
   * Fehler melden — aber erst nach ERROR_HOLD_MS („Vorgabe ~3 s").
   *
   * Grund: `session.step.failed` (und `session.execution.failed`) trifft VOR
   * dem Abbruch ein. Bricht der Nutzer den Turn selbst ab oder wird der Dienst
   * neu gestartet, kommt in der Zwischenzeit `session.execution.interrupted`
   * bzw. `location.shutdown` — dann wird die Meldung verworfen statt verschickt.
   * Ein Abbruch-Merkmal im Ereignis unterdrückt sie sofort. Weitere Fehlschläge
   * derselben Session im Fenster ergeben zusammen EINE Meldung.
   */
  async function reportError(sessionID, event) {
    if (!sessionID) return;
    if (isAbortEvent(event)) {
      cancelPendingError(sessionID);
      diag("fehler-verworfen", "abbruch im ereignis");
      return;
    }
    const pending = pendingErrors.get(sessionID);
    if (pending) {
      pending.count += 1;
      return;
    }
    const entry = { event, count: 1, timer: null };
    entry.timer = setTimeout(() => {
      pendingErrors.delete(sessionID);
      flushError(sessionID, entry).catch((error) => {
        logger("Fehlermeldung nicht verschickt:", error?.message ?? String(error));
      });
    }, errorHoldMs);
    // Ein offener Fehler-Timer darf den Prozess nicht am Beenden hindern.
    if (typeof entry.timer?.unref === "function") entry.timer.unref();
    pendingErrors.set(sessionID, entry);
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

    // Abbruch und Dienst-Neustart melden nie etwas von sich aus — sie
    // verwerfen nur eine noch zurückgehaltene Fehlermeldung (siehe
    // reportError). Ohne das käme nach einem selbst abgebrochenen Turn oder
    // einem Dienst-Neustart „⚠️ Fehler" ins Topic (der Fehlschlag trifft vor
    // dem Abbruch ein).
    if (type === "session.execution.interrupted") {
      cancelPendingError(sessionID);
      return;
    }
    if (type === "location.shutdown") {
      cancelAllPendingErrors();
      return;
    }

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
      // ── Frage/Freigabe beantwortet: die Session arbeitet weiter ────────
      case "form.replied":
      case "permission.replied": {
        if (sessionID) state.working.add(sessionID);
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
      // Wie der Klick-Link der Meldungen (kein zweiter Aufbau der Basis).
      click: linkFor(""),
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
    // Das tatsächlich verwendete Topic (ggf. mit NTFY_TOPIC_PREFIX): die
    // Aktiv-Zeile in createPluginRuntime nennt damit den echten Wert.
    topic,
    _state: state,
  };
}

// ── V2-Plugin-Definition ───────────────────────────────────────────────────

/**
 * Baut die Plugin-Definition ({ id, setup }).
 * Als Fabrik exportiert, damit Tests Umgebung, argv und fetch vorgeben können.
 */
export function createPluginRuntime(options = {}) {
  const env = options.env ?? process.env;
  // Die Env-Datei gilt für ALLE NTFY_*-Einstellungen, nicht nur für
  // Token/URL/Topic: auch Session-Link (NTFY_SESSION_URL), Serverschlüssel
  // (NTFY_SERVER_KEY) und die Schalter kommen sonst nicht im Service-Prozess
  // an. Einmal lesen (readEnvFile wirft nie) und als Basis mit der
  // Prozess-Env zusammenführen — Priorität bleibt Env > Datei > Default.
  const fileEnv = readEnvFile(env.NTFY_ENV_FILE ?? "/etc/opencode.env");
  const merged = { ...fileEnv, ...env };
  const argv = options.argv ?? process.argv.slice(2);
  const selftest = merged.NTFY_SELFTEST === "1";

  return {
    id: PLUGIN_ID,
    async setup(ctx) {
      // Nur der Hintergrund-Service meldet; im Selbsttest auch ein Test-Server.
      if (!selftest && !isServiceProcess(argv)) return;

      const cfg = resolveConfig(merged);
      const contextDirectory =
        typeof ctx?.location?.directory === "string" ? ctx.location.directory : "";
      const notifier = createNtfyNotifier({
        env: { ...merged, NTFY_TOKEN: cfg.token, NTFY_URL: cfg.url, NTFY_TOPIC: cfg.topic },
        ...(options.fetch ? { fetch: options.fetch } : {}),
        ...(options.log ? { log: options.log } : {}),
        directory: contextDirectory,
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

      // Das tatsächlich verwendete Topic nennen (mit NTFY_TOPIC_PREFIX kann es
      // vom konfigurierten NTFY_TOPIC abweichen) — so ist im Log sofort
      // sichtbar, wohin die Meldungen gehen.
      console.log(
        `[ntfy-notify] aktiv (topic ${notifier.topic}, location ${ctx?.location?.directory ?? "?"})`
      );

      // "Arbeitet gerade": jeder abgeschickte Prompt markiert die Session.
      try {
        await ctx.session.hook("prompt", (event) => {
          notifier.markWorking(event?.sessionID ?? event?.session?.id);
        });
      } catch (error) {
        console.error("[ntfy-notify] prompt-Hook nicht registriert:", error?.message ?? error);
      }

      const controller = new AbortController();
      // Diagnose des Ereignisstroms in die Log-Datei (NTFY_REPLY_LOG): so ist im
      // Betrieb sichtbar, ob überhaupt Events ankommen und ob der Strom neu
      // aufgebaut wurde.
      const diagStream = makeDiag({ ...merged });
      // Wartezeit vor dem Wiederaufbau: 1 s, bei wiederholtem Fehlschlag bis
      // 5 s (options.eventRetryMs übersteuert das nur in Tests).
      const retryMs =
        Number(options.eventRetryMs) > 0 ? Number(options.eventRetryMs) : EVENT_RETRY_MS;
      // Ereignistypen, die diese Instanz schon diagnostiziert hat — eine Zeile
      // je Typ genügt, sonst würde jeder Delta-Event das Log fluten.
      const seenTypes = new Set();

      /**
       * Ereignisstrom abonnieren und selbst heilen: endet der Strom (z. B.
       * nach einem Plugin-Neuladen) oder wirft er, wird er nach kurzer
       * Wartezeit neu aufgebaut, statt still zu sterben. Ein Fehler beendet
       * die Schleife also nie dauerhaft; nur der Abbruch des Controllers
       * (teardown) beendet sie sauber.
       */
      void (async () => {
        let failures = 0;
        while (!controller.signal.aborted) {
          let gotEvents = false;
          try {
            diagStream("event-strom-start", "");
            for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
              if (controller.signal.aborted) break;
              gotEvents = true;
              // Gedrosselte Diagnose: nur der erste Event je Typ (und Instanz).
              const type = typeof event?.type === "string" && event.type ? event.type : "?";
              if (!seenTypes.has(type)) {
                seenTypes.add(type);
                diagStream("ereignis", type);
              }
              try {
                await notifier.handleEvent(event);
              } catch (error) {
                console.error("[ntfy-notify] Event-Fehler:", error?.message ?? error);
              }
            }
            diagStream("event-strom-ende", "ende");
          } catch (error) {
            diagStream("event-strom-ende", error?.message ?? String(error));
            if (!controller.signal.aborted) {
              console.error("[ntfy-notify] Event-Stream beendet:", error?.message ?? error);
            }
          }
          if (controller.signal.aborted) break;
          // Nach einem Strom mit Ereignissen wieder bei 1 s anfangen, sonst
          // wächst die Wartezeit bei wiederholtem Sofort-Ende bis 5 s.
          failures = gotEvents ? 0 : failures + 1;
          await sleep(
            Math.min(retryMs * Math.max(1, failures), EVENT_RETRY_MAX_MS),
            controller.signal
          );
        }
      })();

      return () => {
        // Der Abbruch beendet die Ereignis-Schleife (siehe event-strom-ende).
        controller.abort();
      };
    },
  };
}

export default createPluginRuntime();
