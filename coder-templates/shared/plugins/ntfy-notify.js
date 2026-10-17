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
//   NTFY_SERVER_KEY   Optionaler Override; sonst base64url des Ursprungs der
//                     Basis. Serverschlüssel der OpenCode-Oberfläche im
//                     Klick-Link (<basis>/server/<schlüssel>/session/<id>).
//                     Der Kopplungslink bleibt auf der Basis — ohne
//                     Session-Pfad.
//   NTFY_PAIR_BUTTON  "0"/"false" schaltet den Kopplungs-Button ab (Default:
//                     an). Jede Meldung trägt dann einen frisch erzeugten,
//                     5 Minuten gültigen Kopplungslink als view-Button mit
//                     (POST /api/pair über die lokale API); der Link wird
//                     ~120 s zwischengespeichert, damit nicht jede Meldung
//                     einen neuen Code erzeugt.
//   NTFY_IGNORE_TITLES kommagetrennte Teilzeichenketten (getrimmt, Groß-/
//                     Kleinschreibung egal): enthält der Titel einer Session
//                     einen davon, wird für sie gar nichts gemeldet — weder
//                     Frage noch Fertig noch Fehler. Leer/ungesetzt = aus.
//   NTFY_LINK_STYLE   "session" (Default) = Klickziel ist der Session-Deep-Link
//                     (<basis>/server/<schlüssel>/session/<id>); "base" = nur
//                     die Basis (Startseite der Oberfläche, Übergangslösung).
//                     Der Kopplungsknopf bleibt in beiden Fällen auf der Basis.
//   NTFY_STATE_FILE   JSON-Datei für die wenigen Angaben, die einen
//                     Dienst-Neustart überleben müssen (Default
//                     /tmp/opencode/ntfy-state.json): je Topic die zuletzt
//                     benachrichtigte Session, die offene Frage/das offene
//                     Formular und die Leseposition der Brücke (`since`).
//
//   Alle NTFY_*-Einstellungen liest das Plugin zusätzlich aus der Env-Datei
//   /etc/opencode.env (Pfad über NTFY_ENV_FILE änderbar): die Service-Umgebung
//   kommt nicht in jeder Installation im Service-Prozess an. Vorrang hat immer
//   die Prozess-Umgebung, danach die Datei, dann die Defaults.
//
// Antwort-Brücke (der Nutzer antwortet in der ntfy-App auf eine Meldung; diese
// Antwort landet als normale Nachricht im Topic und wird hier abgeholt und als
// Prompt in die zuletzt benachrichtigte Session eingespeist):
//   NTFY_REPLY_ENABLED      "0"/"false" schaltet die Brücke ab (Default: an)
//   NTFY_REPLY_TOPIC        Topic zum Abholen (Default NTFY_TOPIC, also "ai")
//   (Abgeholt wird über den ntfy-Stream GET /<topic>/json?since=<id>: die
//    Verbindung bleibt offen und liefert die Zeilen laufend. Endet sie, wird
//    sie nach kurzer Wartezeit neu aufgebaut — bei HTTP 429 deutlich später.)
//   Die Zuordnung „Session der letzten Meldung“ und der `since`-Zeiger liegen
//   zusätzlich in NTFY_STATE_FILE: nach einem Dienst-Neustart findet eine
//   Handy-Antwort ihre Session auch ohne In-Memory-Zustand, und der Strom
//   setzt an der letzten Leseposition an (keine alten Meldungen erneut).
//   NTFY_FORM_REPLY         "0"/"false" schaltet die direkte Antwort in eine
//                           offene native Frage ab (Default: an). Aus heißt:
//                           kein Formular-Abruf, nur Prompt-Einspeisung — dann
//                           bleibt nie ein Formular offen, das die Brücke hätte
//                           schließen müssen (kein Deadlock-Risiko).
//
// Ist in der Ziel-Session eine native Frage offen (question-Tool =
// serverseitiges Formular), trägt die Brücke die Antwort über die lokale API
// direkt in das Formular ein statt einen Prompt einzuspeisen — der blockierte
// Turn läuft dann weiter, als hätte der Nutzer im Fragenfenster geantwortet.
// Die Zuordnung ist bewusst streng: nur exakter Treffer auf Optionswert/-label
// oder eine reine Zahl als Optionsindex — keine unscharfe Teilstring-Suche, die
// ganze Sätze falsch zuordnen könnte. Ohne eindeutigen Treffer wird der Rohtext
// dennoch versucht (falls das Feld Strings akzeptiert); lehnt die API ab, wird
// die offene Frage abgebrochen (DELETE .../form/{id}) und der Text als Prompt
// eingespeist, damit kein Turn blockiert zurückbleibt. Mehrere Fragen/Felder
// werden konservativ ebenso abgebrochen; Freigabe-Dialoge bleiben dem nativen
// Fenster vorbehalten. Jeder Fehler endet trotzdem in der Prompt-Einspeisung.
// Das native Fragenfenster bleibt unverändert.
// Zugang zur lokalen API: HTTP-Basic "opencode:<Passwort>" gegen
// http://127.0.0.1:${NTFY_SESSION_PORT} (Default 49374); das Passwort wird zur
// Laufzeit aus OPENCODE_SERVER_PASSWORD, /etc/opencode.env oder der
// Service-Datei gelesen und nie geloggt.
//
// Kopplungs-Button: jede Meldung trägt zusätzlich einen frisch erzeugten
// Kopplungslink (POST /api/pair, der Code gilt 5 Minuten) als view-Aktion mit,
// damit der Nutzer unterwegs direkt koppeln kann. Der fertige Link wird ~120 s
// im geteilten Laufzeit-Zustand zwischengespeichert — es entsteht also nicht
// für jede Meldung ein neuer Code. Schlägt die Erzeugung fehl (Netz, Status,
// kein Passwort), geht die Meldung ohne Button raus und der Fehler landet als
// "pair-link-fehler" im Diagnose-Log (NTFY_REPLY_LOG); die Meldung selbst
// scheitert nie daran.
//
// Installation: coder-templates/shared/plugins/ntfy-notify.js
//               → ~/.config/opencode/plugins/ (dort automatisch geladen)

import { appendFileSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const DEFAULT_URL = "https://ntfy.mueller-nas.de";
const DEFAULT_TOPIC = "ai";
// Rückfall-Serverschlüssel im Klick-Link (/server/<schlüssel>/session/<id>),
// wenn sich der Ursprung der Basis nicht als URL auswerten lässt; sonst zählt
// base64url des Ursprungs der Basis (siehe serverKeyFromBase).
const DEFAULT_SERVER_KEY = "local";
// Zustandsdatei: wenige Angaben, die einen Dienst-Neustart überleben müssen
// (Zuordnung Topic → Session, offene Frage/offenes Formular, Leseposition).
const DEFAULT_STATE_FILE = "/tmp/opencode/ntfy-state.json";
// Gedrosseltes Schreiben: höchstens ein Schreibvorgang je Sekunde.
const STATE_WRITE_MS = 1000;
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
// Bestätigung, wenn die Antwort direkt in der offenen Frage gelandet ist.
// Beginnt wie die normale Bestätigung mit "↩️", damit die Brücke die eigene
// Nachricht im Poll wieder als eigene erkennt (REPLY_PREFIXES).
const FORM_REPLY_ACK_TITLE = "↩️ Antwort in die offene Frage eingetragen";
// Bestätigung, wenn die offene Frage abgebrochen und der Text als normale
// Nachricht übergeben wurde (der Agent liest den Satz dann selbst).
const FORM_CANCELLED_ACK_TITLE = "↩️ Frage aufgelöst, Antwort als Nachricht übergeben";
// Hinweiszeile in der Frage-Meldung (die mit den nummerierten Optionen).
const FORM_QUESTION_HINT = "Antwort direkt hier möglich: Nummer der Option oder freier Text.";
// Zugang zur lokalen OpenCode-API (HTTP-Basic, Nutzer "opencode").
const FORM_API_USER = "opencode";
const FORM_API_TIMEOUT_MS = 5000;
// Abgeholt wird der ntfy-Stream des Topics (siehe startReplyBridge); die
// Wartezeiten gelten für den Wiederaufbau nach einem Abbruch.
const REPLY_RECONNECT_MS = 1000; // Grundwartezeit vor dem Neuverbinden
const REPLY_RECONNECT_MAX_MS = 30000; // Obergrenze dieser Wartezeit
// HTTP 429 heißt: ntfy drosselt diesen Aufrufer. Dann deutlich länger warten —
// eine schnelle Wiederholung treibt das Limit nur weiter hoch.
const REPLY_RATE_LIMIT_MS = 30000; // erste Wartezeit nach einer Drosselung
const REPLY_RATE_LIMIT_MAX_MS = 300000; // Obergrenze (5 min)
// Nur der Verbindungsaufbau hat ein Zeitlimit; steht der Strom, bleibt er
// offen (ntfy schickt alle ~45 s eine keepalive-Zeile).
const REPLY_CONNECT_TIMEOUT_MS = 30000;
const MAX_REPLY_TEXT = 2000;
const MAX_SENT_IDS = 200;
// Schon verarbeitete Fremd-IDs — gleiche Begrenzung wie bei den eigenen IDs.
const MAX_PROCESSED_IDS = 200;
// Kopplungslink: `opencode pair` erzeugt einen einmaligen Code, der 5 Minuten
// gilt. Er hängt als view-Button an jeder Meldung; der fertige Link wird ~120 s
// zwischengespeichert (Codes sind einmalig, aber so lange gültig).
const PAIR_API_TIMEOUT_MS = 5000;
const PAIR_LINK_TTL_MS = 120000;
const PAIR_LINK_LABEL = "Koppeln (5 Min)";
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
 * und die Schalter (`NTFY_PAIR_BUTTON`, `NTFY_FORM_REPLY` …). Die
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

/**
 * Diagnose-Zeile anhängen — nur wenn NTFY_REPLY_LOG gesetzt ist (dieselbe
 * Datei wie die Antwort-Brücke, also eine Diagnose-Quelle). Wirft nie.
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

/** "0"/"false"/"no"/"off" schalten den Kopplungs-Button ab (Default: an). */
function pairEnabled(env) {
  const value = String(env.NTFY_PAIR_BUTTON ?? "1")
    .trim()
    .toLowerCase();
  return !["0", "false", "no", "off"].includes(value);
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

/** Formular-ID eines Frage-/Freigabe-Events (tolerant wie sessionInfo). */
function formIdFromEvent(event) {
  for (const candidate of candidates(event)) {
    const direct = candidate.formID ?? candidate.formId;
    if (typeof direct === "string" && direct) return direct;
    const form = candidate.form;
    if (form && typeof form === "object" && typeof form.id === "string" && form.id) return form.id;
  }
  return "";
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
    // Die eine Antwort-Brücke des Prozesses (siehe startReplyBridge): jede
    // weitere Plugin-Instanz benutzt sie mit, statt eine eigene zu starten.
    bridge: null,
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

/** Nur für Tests: geteilten Zustand zurücksetzen. */
export function resetSharedState() {
  for (const map of Object.values(SHARED)) {
    if (map instanceof Map || map instanceof Set) map.clear();
  }
  const reply = replyRuntime();
  if (Array.isArray(reply.sentIds)) reply.sentIds.length = 0;
  else if (reply.sentIds instanceof Set) reply.sentIds.clear();
  if (Array.isArray(reply.processedIds)) reply.processedIds.length = 0;
  else if (reply.processedIds instanceof Set) reply.processedIds.clear();
  reply.lastSessionID = "";
  reply.lastActiveSession = "";
  reply.replySince = "";
  // Zwischenspeicher des Kopplungslinks mit zurücksetzen: im Betrieb bleibt er
  // absichtlich erhalten, Tests starten damit aber sauber.
  reply.pairLink = "";
  reply.pairLinkAt = 0;
  reply.pairLinkTtl = PAIR_LINK_TTL_MS;
  // Eine noch laufende Schleife mit beenden — sonst würde sie nach dem
  // Zurücksetzen weiterpollen und wäre über stop() nicht mehr erreichbar.
  try {
    reply.replyAbort?.abort();
  } catch {
    /* Zurücksetzen darf nie werfen */
  }
  reply.replyAbort = null;
  reply.replyPromise = null;
  // Die eine Brücke des Prozesses mit beenden und den Eintrag freigeben: sonst
  // liefe nach dem Zurücksetzen eine Schleife weiter, die über stop() nicht
  // mehr erreichbar wäre. Der nächste Start fängt wieder bei refs = 1 an.
  const bridge = SHARED.bridge;
  if (bridge) {
    try {
      bridge.stop();
    } catch {
      /* Zurücksetzen darf nie werfen */
    }
  }
  SHARED.bridge = null;
}

// ── Prozessglobaler Zustand der Antwort-Brücke ────────────────────────────
//
// Dieselbe Begründung wie beim Event-Zustand: mehrere Plugin-Instanzen (eine
// je Location) teilen sich einen Prozess. sentIds/lastSessionID liegen deshalb
// am Prozess und werden vom Notifier (send/sendOnce) genauso benutzt wie von
// der Brücke selbst.
//
// Die Schleife darf je Prozess nur EINMAL laufen — und zwar genau einmal, auch
// wenn nacheinander viele Instanzen laden: deshalb hält SHARED.bridge die
// laufende Brücke samt Referenzzähler (siehe startReplyBridge). Früher startete
// jede Instanz ihre eigene Schleife; im Diagnoseprotokoll war das an drei
// verschiedenen `since`-Token zu sehen (drei Brücken, zusammen bis ~35
// Anfragen/Minute) — bis ntfy mit HTTP 429 drosselte. replyAbort/replyPromise
// halten weiterhin die aktuelle Schleife fest, replySince (letzter
// `since`-Zeiger) und lastActiveSession (Fallback-Ziel) überleben den Wechsel
// bewusst.

const REPLY_KEY = Symbol.for("ntfy.notify.runtime");

function newReplyState() {
  return {
    sentIds: [], // IDs der selbst gesendeten ntfy-Nachrichten
    processedIds: [], // IDs der bereits verarbeiteten Fremd-Nachrichten (Idempotenz)
    lastSessionID: "", // Session der zuletzt gesendeten Benachrichtigung
    lastActiveSession: "", // zuletzt gesehene Session ohne Benachrichtigung (Fallback-Ziel)
    replyAbort: null, // AbortController der aktuell laufenden Brücken-Schleife
    replyPromise: null, // Promise dieser Schleife (erledigt sich mit ihr)
    replySince: "", // letzter `since`-Zeiger; überlebt Schleifen-Neustarts
    pairLink: "", // zwischengespeicherter Kopplungslink (5-Minuten-Code)
    pairLinkAt: 0, // Zeitstempel der Erzeugung (ms)
    pairLinkTtl: PAIR_LINK_TTL_MS, // Gültigkeit des Zwischenspeichers (ms)
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
  if (typeof existing.lastActiveSession !== "string") existing.lastActiveSession = "";
  if (typeof existing.replySince !== "string") existing.replySince = "";
  if (typeof existing.pairLink !== "string") existing.pairLink = "";
  if (typeof existing.pairLinkAt !== "number") existing.pairLinkAt = 0;
  if (typeof existing.pairLinkTtl !== "number") existing.pairLinkTtl = PAIR_LINK_TTL_MS;
  if (!("replyAbort" in existing)) existing.replyAbort = null;
  if (!("replyPromise" in existing)) existing.replyPromise = null;
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

// ── Persistenter Zustand (überlebt einen Dienst-Neustart) ──────────────────
//
// Ein Dienst-Neustart verliert den Zustand an globalThis: im Protokoll stand
// danach `aktion: no-session`, eine Handy-Antwort fand ihre Session nicht mehr.
// Wenige Angaben liegen deshalb zusätzlich in einer kleinen JSON-Datei
// (NTFY_STATE_FILE, Default /tmp/opencode/ntfy-state.json), je Topic:
//   lastSessionId  Session, die zuletzt eine Meldung in diesem Topic erzeugt hat
//   openQuestionId offene native Frage (ID des Formulars)
//   openFormId     offenes Formular (z. B. Freigabe-Dialog)
//   since          Leseposition der Antwort-Brücke (`since`-Zeiger)
//
// Geschrieben wird gedrosselt (höchstens einmal je Sekunde), gelesen einmal
// beim Anlegen des Speichers. Eine fehlende, kaputte oder halb gefüllte Datei
// ist kein Fehler — dann arbeitet der Speicher wie bisher nur im Arbeitsspeicher;
// Schreibfehler werden ausschließlich diagnostiziert.

const STATE_VERSION = 1;

/** Pfad der Zustandsdatei (NTFY_STATE_FILE, sonst der Default). */
function stateFilePath(env = {}) {
  const configured = String(env.NTFY_STATE_FILE ?? "").trim();
  return configured || DEFAULT_STATE_FILE;
}

function emptyTopicState() {
  return { lastSessionId: "", openQuestionId: "", openFormId: "", since: "" };
}

/** Tolerante Form: nur bekannte Felder mit String-Wert werden übernommen. */
function normalizeTopicState(value) {
  const out = emptyTopicState();
  if (!value || typeof value !== "object") return out;
  for (const key of Object.keys(out)) {
    if (typeof value[key] === "string") out[key] = value[key];
  }
  return out;
}

/**
 * Zustandsspeicher für die Angaben, die einen Dienst-Neustart überleben müssen.
 * Wirft nie: fehlende/kaputte Dateien und fehlgeschlagene Schreibvorgänge ändern
 * nur den gespeicherten Zustand, nie den Betrieb.
 */
export function createStateStore(options = {}) {
  const env = options.env ?? {};
  const path = String(options.path ?? stateFilePath(env));
  const now = options.now ?? (() => Date.now());
  const log = options.log ?? (() => {});
  const throttle = Number(options.throttleMs);
  const writeMs = Number.isFinite(throttle) && throttle >= 0 ? throttle : STATE_WRITE_MS;
  const topics = new Map();
  // Topic → Felder, die dieser Prozess selbst geschrieben hat. Je Feld: der
  // `since`-Zeiger der Brücke darf die persistierte Session-Zuordnung nicht
  // entwerten (und umgekehrt) — beide werden unabhängig fortgeschrieben.
  const written = new Map();
  let loaded = false;
  let dirty = false;
  let lastWriteAt = 0;
  let timer = null;

  /** Datei einmal einlesen; kaputte/fehlende Datei = leerer Speicher. */
  function load() {
    if (loaded) return;
    loaded = true;
    let data;
    try {
      data = JSON.parse(readFileSync(path, "utf8"));
    } catch {
      return; // Datei fehlt oder ist kein JSON – wie ohne Datei weiterarbeiten
    }
    const saved = data?.topics;
    if (!saved || typeof saved !== "object") return;
    for (const [topic, value] of Object.entries(saved)) {
      if (topic) topics.set(topic, normalizeTopicState(value));
    }
  }

  function topicState(topic) {
    load();
    const name = String(topic ?? "");
    if (!topics.has(name)) topics.set(name, emptyTopicState());
    return topics.get(name);
  }

  /** Dateiinhalt als einfaches Objekt — leere Einträge bleiben draußen. */
  function snapshot() {
    const out = {};
    for (const [topic, value] of topics) {
      const state = normalizeTopicState(value);
      if (Object.values(state).some(Boolean)) out[topic] = state;
    }
    return { version: STATE_VERSION, topics: out };
  }

  function write() {
    try {
      mkdirSync(dirname(path), { recursive: true });
      // Erst daneben schreiben, dann umbenennen: ein Abbruch mitten im Schreiben
      // hinterlässt so keine halbe Datei.
      const temp = `${path}.tmp`;
      writeFileSync(temp, `${JSON.stringify(snapshot(), null, 2)}\n`, { mode: 0o600 });
      renameSync(temp, path);
      return true;
    } catch (error) {
      // Schreibfehler nur diagnostizieren — der Betrieb läuft ohne Datei weiter.
      log("Zustandsdatei nicht geschrieben:", error?.message ?? String(error));
      return false;
    }
  }

  function schedule(delayMs) {
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      flush();
    }, Math.max(0, delayMs));
    // Ein offener Schreib-Timer darf den Prozess nicht am Ende hindern.
    if (typeof timer?.unref === "function") timer.unref();
  }

  /** Den geänderten Stand sichern — gedrosselt, mit force sofort. */
  function flush(force = false) {
    if (!dirty) return false;
    const elapsed = lastWriteAt === 0 ? Infinity : now() - lastWriteAt;
    if (!force && elapsed < writeMs) {
      schedule(writeMs - elapsed);
      return false;
    }
    dirty = false;
    lastWriteAt = now();
    return write();
  }

  return {
    path,
    /** Stand eines Topics (immer ein Objekt, notfalls leer). */
    topic: (name) => ({ ...topicState(name) }),
    /** Ein Feld lesen; alles Unerwartete wird zu "". */
    get(name, key) {
      const value = topicState(name)?.[key];
      return typeof value === "string" ? value : "";
    },
    /** Felder setzen (nur bekannte, nur Strings) und gedrosselt schreiben. */
    set(name, patch) {
      const state = topicState(name);
      const topicName = String(name ?? "");
      let changed = false;
      for (const [key, value] of Object.entries(patch ?? {})) {
        if (!(key in state) || typeof value !== "string") continue;
        // Auch ein unveränderter Wert zählt als „von diesem Prozess gesetzt“:
        // der Arbeitsspeicher ist damit die gültige Quelle, und ein alter
        // Dateiwert wird nicht mehr als Zuordnung herangezogen.
        if (!written.has(topicName)) written.set(topicName, new Set());
        written.get(topicName).add(key);
        if (value === state[key]) continue;
        state[key] = value;
        changed = true;
      }
      // Rückgabe: true = der gespeicherte Stand hat sich geändert.
      if (!changed) return false;
      dirty = true;
      flush();
      return true;
    },
    /**
     * true = dieser Prozess hat das Feld (oder das ganze Topic) selbst
     * geschrieben. Dann ist der Arbeitsspeicher die aktuellere Quelle, und der
     * persistierte Wert wird nicht als Zuordnung benutzt.
     */
    writtenHere(name, key) {
      load();
      const fields = written.get(String(name ?? ""));
      if (!fields) return false;
      if (typeof key !== "string" || !key) return fields.size > 0;
      return fields.has(key);
    },
    flush,
  };
}

const STATE_STORE_KEY = Symbol.for("ntfy.notify.stateStores");

/**
 * Zustandsspeicher je Datei — im Prozess geteilt, wie der übrige Plugin-Zustand
 * (das Plugin lebt mehrfach je Prozess, alle Instanzen müssen denselben Speicher
 * sehen). Bewusst NICHT in resetSharedState() geleert: die Angabe „dieser
 * Prozess hat dieses Topic geschrieben“ muss über alle Instanzen erhalten
 * bleiben, sonst würde nach einem Reset ein alter Dateiwert als Zuordnung gelten.
 */
export function stateStoreFor(env = {}) {
  const registry = (globalThis[STATE_STORE_KEY] ??= new Map());
  const path = stateFilePath(env);
  if (!registry.has(path)) registry.set(path, createStateStore({ env, path }));
  return registry.get(path);
}

/** Nur für Tests: die geteilten Zustandsspeicher vergessen (Datei bleibt). */
export function resetStateStores() {
  globalThis[STATE_STORE_KEY] = new Map();
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
  // Zustandsspeicher für die Angaben, die einen Dienst-Neustart überleben
  // (Zuordnung der Session, offene Frage, Leseposition der Brücke).
  const stateStore = options.stateStore ?? stateStoreFor(env);
  // Stummschaltung per Titel (NTFY_IGNORE_TITLES) und Klickziel-Stil
  // (NTFY_LINK_STYLE) einmal auswerten — die Env ändert sich im Betrieb nicht.
  const mutedTitles = ignoredTitleParts(env);
  const baseOnlyLinks = linkStyle(env) === "base";
  // Kopplungs-Button: Schalter, Diagnose-Ziel und API-Passwort. Die Test-Vorgabe
  // (apiPassword) hat Vorrang; sonst wird das Passwort — wie bei der
  // Antwort-Brücke — zur Laufzeit ermittelt und nie geloggt.
  const pairButton = options.pairButton ?? pairEnabled(env);
  const diag = options.diag ?? makeDiag(env);
  let resolvedPassword;
  function serverPassword() {
    if (typeof options.apiPassword === "string") return options.apiPassword;
    if (resolvedPassword === undefined) resolvedPassword = resolveServerPassword(env);
    return resolvedPassword;
  }

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

  /**
   * Basis der Session-Links — linkFor baut darauf den Session-Pfad auf, der
   * Kopplungslink hängt direkt hier an (deshalb ohne Session-Pfad).
   * NTFY_SESSION_URL gewinnt immer, sonst wird die Port-App der laufenden
   * `opencode serve`-Instanz abgeleitet.
   */
  function linkBase(sessionID) {
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

  /**
   * Klick-Ziel jeder Meldung: Session-Deep-Link der OpenCode-Oberfläche
   * (`/server/<serverKey>/session/<id>`, Solid-Router mit Path-Routing). Der
   * Serverschlüssel ist NTFY_SERVER_KEY, wenn gesetzt — sonst base64url des
   * Ursprungs der Basis (genau der Wert, den die Oberfläche verwendet; siehe
   * serverKeyFromBase). Die Session-ID wird URL-sicher eingesetzt. Ohne
   * Session-ID bleibt es bei der Basis; der Kopplungslink (pairLink) nutzt
   * weiterhin linkBase und bekommt keinen Session-Pfad. NTFY_LINK_STYLE=base
   * setzt das Klickziel auf die Basis (Übergangslösung, siehe linkStyle).
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

  /**
   * Kopplungslink über die lokale API erzeugen: POST /api/pair liefert einen
   * einmaligen Code (5 Minuten gültig); daraus wird <basis>/auth/connect/<code>.
   * Der Auth-Kopf wird nie geloggt. Rückgabe: { url, ttlMs } oder null — wirft
   * nie, ein Fehler heißt schlicht "kein Button".
   */
  async function createPairingLink(base) {
    if (typeof fetchImpl !== "function") return null;
    const password = serverPassword();
    if (!password) {
      diag("pair-link-fehler", "kein API-Passwort");
      return null;
    }
    const init = {
      method: "POST",
      headers: { Authorization: basicAuth(FORM_API_USER, password) },
    };
    const signal = timeoutSignal(PAIR_API_TIMEOUT_MS);
    if (signal) init.signal = signal;
    try {
      const res = await fetchImpl(`${localApiUrl(env)}/api/pair`, init);
      const status = Number(res?.status ?? 0);
      if (res?.ok === false || (status && status >= 400)) throw new Error(`HTTP ${status || "?"}`);
      const data = typeof res?.json === "function" ? await res.json() : undefined;
      const code = typeof data?.code === "string" ? data.code.trim() : "";
      if (!code) throw new Error("Antwort ohne code");
      // expires_in der API vorsichtig auswerten: der Link gilt nie länger als
      // der Zwischenspeicher, aber auch nie länger als der Code selbst.
      const expires = Number(data?.expires_in) > 0 ? Number(data.expires_in) * 1000 : 0;
      return {
        url: `${base.replace(/\/+$/, "")}/auth/connect/${encodeURIComponent(code)}`,
        ttlMs: expires > 0 ? Math.min(PAIR_LINK_TTL_MS, expires) : PAIR_LINK_TTL_MS,
      };
    } catch (error) {
      logger("Kopplungslink nicht erzeugt:", error?.message ?? String(error));
      diag("pair-link-fehler", error?.message ?? String(error));
      return null;
    }
  }

  /**
   * Kopplungslink mit kurzem Zwischenspeicher: der Code ist einmalig, gilt aber
   * 5 Minuten — deshalb wird der fertige Link ~120 s wiederverwendet, statt für
   * jede Meldung einen neuen Code zu erzeugen. Der Zwischenspeicher liegt am
   * geteilten Laufzeit-Zustand und überlebt damit auch einen Plugin-Reload.
   */
  async function pairLink(sessionID) {
    const base = linkBase(sessionID).replace(/\/+$/, "");
    const cached = typeof replyState.pairLink === "string" ? replyState.pairLink : "";
    const at = Number(replyState.pairLinkAt ?? 0);
    const stored = Number(replyState.pairLinkTtl ?? 0);
    const ttl = stored > 0 ? stored : PAIR_LINK_TTL_MS;
    // Nur Links zur selben Basis wiederverwenden: mehrere Locations teilen sich
    // einen Prozess, können aber unterschiedliche Basen haben.
    if (cached.startsWith(`${base}/auth/connect/`) && now() - at < ttl) return cached;
    const fresh = await createPairingLink(base);
    if (!fresh) return "";
    replyState.pairLink = fresh.url;
    replyState.pairLinkAt = now();
    replyState.pairLinkTtl = fresh.ttlMs;
    return fresh.url;
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
      title: oneLine(payload.title, MAX_TITLE),
      priority: payload.priority ?? 3,
      tags: payload.tags ?? ["speech_balloon"],
    };
    // Aktionen aufbauen (ntfy erlaubt höchstens drei je Nachricht): vorhandene
    // übernehmen, dann das Klick-Ziel, zuletzt den Kopplungslink. Das Klick-Ziel
    // selbst bleibt unverändert.
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
    // Kopplungslink als view-Button — nur wenn die Erzeugung geklappt hat und
    // dieselbe URL nicht schon als Aktion dranhängt. Fehler ändern die Meldung
    // nicht: createPairingLink fängt alles ab und protokolliert nur; die
    // Meldung selbst darf nie scheitern.
    if (pairButton) {
      try {
        const pairUrl = await pairLink(sessionID);
        if (
          pairUrl &&
          actions.length < 3 &&
          !actions.some((action) => action.action === "view" && action.url === pairUrl)
        ) {
          actions.push({ action: "view", label: PAIR_LINK_LABEL, url: pairUrl });
        }
      } catch (error) {
        logger("Kopplungslink fehlgeschlagen:", error?.message ?? String(error));
        diag("pair-link-fehler", error?.message ?? String(error));
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

  /** Stummgeschaltete Session? (Titel enthält einen Teil aus NTFY_IGNORE_TITLES) */
  function isMuted(sessionID) {
    return titleMuted(mutedTitles, metaFor(sessionID).title);
  }

  /**
   * Offenes Formular im Zustandsspeicher merken (openQuestionId für Fragen,
   * openFormId für Freigaben): So ist nach einem Dienst-Neustart noch bekannt,
   * welche Frage offen war.
   */
  function rememberOpenForm(key, event, sent) {
    if (!sent) return;
    const formID = formIdFromEvent(event);
    if (formID) stateStore.set(topic, { [key]: formID });
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
    const sent = await send(payload, sessionID);
    // Ziel der Antwort-Brücke: die Session der zuletzt zugestellten Meldung.
    if (sent && typeof sessionID === "string" && sessionID) {
      replyState.lastSessionID = sessionID;
      // Dieselbe Zuordnung in die Zustandsdatei: nach einem Dienst-Neustart
      // findet die Brücke die Session ohne In-Memory-Zustand wieder.
      stateStore.set(topic, { lastSessionId: sessionID });
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

    // Fallback-Ziel der Antwort-Brücke: die zuletzt gesehene Session, die kein
    // Subagent ist (die Prüfung darüber ist synchron). So kommt eine Antwort
    // auch dann an, wenn die Session keine eigene Meldung ausgelöst hat.
    // lastSessionID (aus Benachrichtigungen) hat in der Brücke Vorrang.
    if (sessionID) replyState.lastActiveSession = sessionID;

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
        // Hinweiszeile: der Text kann direkt aus ntfy in die offene Frage
        // eingetragen werden (siehe Formular-Antwort in der Antwort-Brücke).
        const hint = formEnabled(env) ? FORM_QUESTION_HINT : "";
        const sent = await sendOnce(sessionID, "question", {
          title: `❓ Frage — ${projectFor(sessionID)}`,
          message: withHeader(
            sessionID,
            [questionBody(event) || fallbackBody(event), hint].filter(Boolean).join("\n\n")
          ),
          priority: 4,
          tags: ["question"],
          click: linkFor(sessionID),
          maxBody: MAX_QUESTION_BODY,
        });
        rememberOpenForm("openQuestionId", event, sent);
        return;
      }
      case "permission.asked":
      case "permission.requested": {
        // Subagenten fordern keine Freigaben an, die der Nutzer sehen müsste.
        if (!sessionID || (await isSubagent(sessionID))) return;
        markWorking(sessionID);
        await ensureMeta(sessionID);
        const sent = await sendOnce(sessionID, "permission", {
          title: `🔐 Freigabe — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, permissionBody(event) || fallbackBody(event)),
          priority: 4,
          tags: ["lock"],
          click: linkFor(sessionID),
          maxBody: MAX_QUESTION_BODY,
        });
        rememberOpenForm("openFormId", event, sent);
        return;
      }
      // ── Frage/Formular beantwortet: nichts mehr offen ──────────────────
      case "form.replied":
      case "permission.replied": {
        stateStore.set(topic, { openQuestionId: "", openFormId: "" });
        if (sessionID) state.working.add(sessionID);
        return;
      }
      case "question.replied":
      case "form.resolved": {
        stateStore.set(topic, { openQuestionId: "", openFormId: "" });
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
// fach im Strom auftaucht. Die reine Logik steckt in createReplyBridge() und ist
// damit ohne Netzwerk testbar; startReplyBridge() hängt die Schleife dran — je
// Prozess genau eine, geteilt über einen Referenzzähler.

/** "0"/"false"/"no"/"off" schalten die Brücke ab (Default: an). */
function replyEnabled(env) {
  const value = String(env.NTFY_REPLY_ENABLED ?? "1")
    .trim()
    .toLowerCase();
  return !["0", "false", "no", "off"].includes(value);
}

// ── Formular-Antwort: Handy-Text → offene native Frage ────────────────────
//
// Die native Frage ist serverseitig ein Formular (question-Tool). Offene
// Formulare einer Session liefert GET /api/session/{id}/form, die Antwort geht
// per POST /api/session/{id}/form/{formID}/reply mit { answer: { key: wert } }
// raus (204 = angenommen), der Abbruch per DELETE auf denselben Pfad ohne
// /reply. Alles hier ist bewusst fehlertolerant und kennt keinen Zustand:
// schlägt etwas fehl, wird nicht geraten — am Ende steht immer die
// Prompt-Einspeisung, und eine offene Frage wird vorher abgebrochen, damit kein
// Turn blockiert zurückbleibt.

/** "0"/"false"/"no"/"off" schalten die Formular-Antwort ab (Default: an). */
function formEnabled(env) {
  const value = String(env.NTFY_FORM_REPLY ?? "1")
    .trim()
    .toLowerCase();
  return !["0", "false", "no", "off"].includes(value);
}

/** Basis-URL der lokalen API (die laufende `opencode serve`-Instanz). */
function localApiUrl(env) {
  const fixed = String(env.OPENCODE_SERVER_URL ?? "").trim();
  if (fixed) return fixed.replace(/\/+$/, "");
  return `http://127.0.0.1:${env.NTFY_SESSION_PORT || "49374"}`;
}

/** HTTP-Basic-Header-Wert ("opencode:<Passwort>"). Wirft nie. */
function basicAuth(user, password) {
  const raw = `${user}:${password}`;
  try {
    const buffer = globalThis.Buffer;
    if (buffer?.from) return `Basic ${buffer.from(raw, "utf8").toString("base64")}`;
    return `Basic ${btoa(raw)}`;
  } catch {
    return "";
  }
}

/** AbortSignal mit Zeitlimit; fehlt die Unterstützung, dann ohne Limit. */
function timeoutSignal(ms) {
  try {
    return typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function"
      ? AbortSignal.timeout(ms)
      : undefined;
  } catch {
    return undefined;
  }
}

/** Erstes Passwort-Feld in einem (verschachtelten) Service-Objekt. */
function findPasswordValue(value, depth = 0) {
  if (depth > 4 || !value || typeof value !== "object") return "";
  for (const [key, child] of Object.entries(value)) {
    if (typeof child === "string") {
      if (/password/i.test(key) && child.trim()) return child.trim();
    } else if (child && typeof child === "object") {
      const found = findPasswordValue(child, depth + 1);
      if (found) return found;
    }
  }
  return "";
}

/**
 * Passwort der lokalen API zur Laufzeit ermitteln: Umgebung → Env-Datei
 * (/etc/opencode.env) → Service-Datei der laufenden Instanz. Der Feldname in
 * der Service-Datei variiert je Version, deshalb wird tolerant gesucht. Der
 * Wert wird nirgends geloggt.
 */
export function resolveServerPassword(env = process.env) {
  const direct = String(env.OPENCODE_SERVER_PASSWORD ?? "").trim();
  if (direct) return direct;
  const fromFile = String(
    readEnvFile(env.NTFY_ENV_FILE ?? "/etc/opencode.env").OPENCODE_SERVER_PASSWORD ?? ""
  ).trim();
  if (fromFile) return fromFile;
  let home = "";
  try {
    home = homedir();
  } catch {
    home = "";
  }
  for (const file of [
    env.OPENCODE_SERVICE_FILE,
    home ? join(home, ".config/opencode/service.json") : "",
    "/root/.config/opencode/service.json",
  ]) {
    if (!file) continue;
    try {
      const found = findPasswordValue(JSON.parse(readFileSync(file, "utf8")));
      if (found) return found;
    } catch {
      /* Datei fehlt / kein JSON – nächster Kandidat */
    }
  }
  return "";
}

/**
 * Optionsliste tolerant normalisieren: laut Schema { value, label }, aber auch
 * einfache Strings sind erlaubt. Einträge ohne Wert fallen raus.
 */
function normalizeOptions(options) {
  const out = [];
  for (const option of Array.isArray(options) ? options : []) {
    if (option === null || option === undefined) continue;
    if (typeof option === "object") {
      const value = option.value ?? option.key ?? option.label ?? option.title;
      if (value === undefined || value === null || value === "") continue;
      out.push({ value, label: String(option.label ?? option.title ?? option.value ?? value) });
      continue;
    }
    out.push({ value: option, label: String(option) });
  }
  return out;
}

function optionText(value) {
  return value === undefined || value === null ? "" : String(value);
}

/**
 * Vergleichsform eines Antworttexts bzw. Optionswerts: Kleinschreibung,
 * Mehrfach-Whitespace zusammengezogen, umschließende Anführungszeichen weg.
 */
function matchKey(value) {
  return optionText(value)
    .trim()
    .replace(/^["'„“”‚‘’`´]+/, "")
    .replace(/["'„“”‚‘’`´]+$/, "")
    .replace(/\s+/g, " ")
    .toLowerCase();
}

/**
 * Antworttext → Optionswert. Bewusst streng: (a) exakte Übereinstimmung mit
 * Optionswert oder -label (ohne Groß-/Kleinschreibung) oder (b) eine reine
 * Zahl n als 1-basierter Optionsindex, sofern 1 ≤ n ≤ Anzahl Optionen.
 * Kein Treffer → null: es wird nicht geraten (eine unscharfe Teilstring-Suche
 * könnte ganze Sätze falsch zuordnen, Stichwort Negation) — der Aufrufer
 * versucht dann den Rohtext bzw. bricht die Frage ab.
 */
function matchOption(options, text) {
  const entries = normalizeOptions(options);
  const needle = matchKey(text);
  if (!needle || !entries.length) return null;
  const exact = entries.find(
    (entry) => matchKey(entry.value) === needle || matchKey(entry.label) === needle
  );
  if (exact) return exact.value;
  if (/^\d+$/.test(needle)) {
    const index = Number(needle) - 1;
    if (index >= 0 && index < entries.length) return entries[index].value;
  }
  return null;
}

/**
 * Beantwortungsplan für das einzige Feld des Formulars.
 * Rückgabe:
 *   { kind: "answer", key, value } — eindeutiger Treffer oder Freitext
 *   { kind: "raw", key }           — Optionsfeld ohne Treffer, das Rohtext
 *                                    akzeptieren könnte (type "string")
 *   null                           — bewusst nicht beantworten
 *
 * Bewusst konservativ: mehrere Felder, unbekannte Feldtypen (number, integer,
 * boolean, external), versteckte/bedingte Felder und mehrdeutige Optionsfelder
 * führen nicht zu einer geratenen Antwort; der Aufrufer bricht dann ab.
 */
function planForForm(form, text) {
  const fields = Array.isArray(form?.fields) ? form.fields : [];
  if (fields.length !== 1) return null;
  const field = fields[0];
  if (!field || typeof field !== "object") return null;
  const key = typeof field.key === "string" ? field.key : "";
  if (!key) return null;
  if (field.hidden === true) return null;
  if (Array.isArray(field.when) && field.when.length) return null;
  const type = typeof field.type === "string" ? field.type : "";
  if (normalizeOptions(field.options).length) {
    const value = matchOption(field.options, text);
    if (value !== null && value !== undefined) {
      // multiselect erwartet laut Schema eine Liste (Form.Value: string[]).
      return { kind: "answer", key, value: type === "multiselect" ? [optionText(value)] : value };
    }
    // Kein eindeutiger Treffer: den Rohtext nur versuchen, wenn das Feld
    // Strings akzeptiert (multiselect erwartet eine Liste).
    return type === "string" ? { kind: "raw", key } : null;
  }
  // Freitext nur beim String-Typ — alles andere bleibt konservativ.
  if (type !== "string") return null;
  return { kind: "answer", key, value: String(text) };
}

/**
 * Nur echte Fragen beantworten: Formulare mit anderem Zweck (z. B.
 * Freigabe-Dialoge) bleiben dem nativen Fenster vorbehalten.
 */
function isQuestionForm(form) {
  const kind = form?.metadata?.kind;
  return typeof kind !== "string" || kind === "" || /^question/i.test(kind);
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

/** Erste brauchbare Zahl (> 0) aus der Liste — sonst der letzte Wert. */
function firstPositive(...values) {
  for (const value of values) {
    const num = Number(value);
    if (Number.isFinite(num) && num > 0) return num;
  }
  return 0;
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
  // Wartezeiten: im Betrieb gelten die Defaults oben (1 s vor dem Neuverbinden,
  // verdoppelnd bis 30 s; nach HTTP 429 deutlich länger). Tests übersteuern sie
  // — reconnectMs ersetzt dabei die früheren idleMs/errorMs, weil es keinen
  // Kurzpoll mehr gibt, sondern nur noch den Wiederaufbau des Stroms.
  const reconnectMs = firstPositive(
    options.reconnectMs,
    options.idleMs,
    options.errorMs,
    REPLY_RECONNECT_MS
  );
  const reconnectMaxMs = firstPositive(options.reconnectMaxMs, REPLY_RECONNECT_MAX_MS);
  const rateLimitMs = firstPositive(options.rateLimitMs, REPLY_RATE_LIMIT_MS);
  const rateLimitMaxMs = firstPositive(options.rateLimitMaxMs, REPLY_RATE_LIMIT_MAX_MS);
  // Zeitlimit nur für den Verbindungsaufbau (Header).
  const connectTimeoutMs = firstPositive(options.connectTimeoutMs, REPLY_CONNECT_TIMEOUT_MS);
  const diagPath = String(env.NTFY_REPLY_LOG ?? "").trim();
  // Formular-Antwort: Schalter, Basis-URL und API-Passwort. Das Passwort
  // (Option apiPassword) hat Vorrang, damit Tests ohne Dateizugriff und ohne
  // Netz laufen können; sonst wird es zur Laufzeit ermittelt. Fehlt es, ist
  // der Formular-Weg still inaktiv und es bleibt bei der Prompt-Einspeisung.
  const formReply = options.formReply ?? formEnabled(env);
  const apiUrl = String(options.apiUrl ?? localApiUrl(env)).replace(/\/+$/, "");
  const apiPassword = String(options.apiPassword ?? resolveServerPassword(env));
  // Zustandsspeicher (Datei): die Zuordnung der Session und die Leseposition
  // überleben damit einen Dienst-Neustart. Fehlt die Datei, ist er schlicht leer.
  const store = options.stateStore ?? stateStoreFor(env);

  /**
   * Diagnose-Log: hängt nur dann Zeilen an NTFY_REPLY_LOG (Pfad) an, wenn die
   * Variable gesetzt ist. Ohne sie keine Datei-Ein-/Ausgabe. Wirft nie.
   */
  function diag(event, detail = "") {
    if (!diagPath) return;
    try {
      appendFileSync(diagPath, `${new Date().toISOString()} ${event}${detail ? `: ${detail}` : ""}\n`);
    } catch {
      /* Diagnose darf die Brücke nie stören */
    }
  }

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
   * Eine Stream-Runde: mit dem ntfy-Stream des Topics verbinden und die
   * NDJSON-Zeilen inkrementell lesen, solange die Verbindung steht. Der
   * Kurzpoll (`?poll=1` alle 5 s) ist damit weg — er kostete bei mehreren
   * Brücken bis ~35 Anfragen/Minute und lief in HTTP 429.
   *
   * `since` bleibt stehen, wenn nichts kam — sonst würde dieselbe Nachricht bei
   * jedem Neuverbinden erneut verarbeitet.
   *
   * Jede Nachricht geht sofort an `onMessage`; `onSince` schreibt den
   * `since`-Zeiger mit, damit ein lange offener Strom nach einem Plugin-Reload
   * an der richtigen Stelle weiterläuft. Ohne `onMessage` werden die
   * Nachrichten stattdessen gesammelt und zurückgegeben (Tests, Direktnutzung) —
   * im Betrieb wüchse der Speicher sonst mit der Laufzeit des Stroms.
   *
   * Rückgabe: { count, messages, nextSince }. Wirft bei Netz-/HTTP-Fehlern;
   * eine Drosselung (HTTP 429) trägt dabei `rateLimited`.
   */
  async function streamOnce(since = "", signal, onMessage = null, onSince = null) {
    if (!url || !token || typeof fetchImpl !== "function") {
      return { count: 0, messages: [], nextSince: since };
    }
    // Der Abbruch des Aufrufers (teardown/Reset) muss den laufenden fetch
    // treffen — deshalb ein eigener Controller, an den beide Signale hängen.
    const connect = new AbortController();
    const abortConnect = () => connect.abort();
    if (signal) signal.addEventListener("abort", abortConnect, { once: true });
    const connectTimer = setTimeout(() => connect.abort(), connectTimeoutMs);
    let res;
    try {
      res = await fetchImpl(
        // Ohne poll=1: der ntfy-Stream hält die Verbindung und liefert die
        // Zeilen laufend nach (format=json ist der NDJSON-Standard des Servers).
        `${url}/${encodeURIComponent(topic)}/json?since=${encodeURIComponent(since)}`,
        { headers: { Authorization: `Bearer ${token}` }, signal: connect.signal }
      );
    } finally {
      clearTimeout(connectTimer);
      if (signal) signal.removeEventListener("abort", abortConnect);
    }
    if (!res?.ok) {
      const status = Number(res?.status ?? 0);
      const error = new Error(`HTTP ${status || "?"}`);
      // 429 = gedrosselt: der Aufrufer soll deutlich länger warten.
      if (status === 429) error.rateLimited = true;
      throw error;
    }

    const messages = [];
    let nextSince = since;
    let count = 0;
    let rest = "";
    const emit = async (line) => {
      const parsed = parseReplyStream(line, nextSince);
      if (parsed.nextSince !== nextSince) {
        nextSince = parsed.nextSince;
        if (onSince) onSince(nextSince);
      }
      for (const message of parsed.messages) {
        count += 1;
        if (onMessage) await onMessage(message);
        else messages.push(message);
      }
    };

    const reader = res.body?.getReader?.();
    if (reader) {
      // Ein Abbruch muss auch den wartenden read() lösen: sonst hinge die
      // Schleife an einer Verbindung, die nie mehr etwas liefert.
      const cancel = () => {
        try {
          const pending = reader.cancel();
          if (pending && typeof pending.catch === "function") pending.catch(() => {});
        } catch {
          /* Abbrechen darf nie werfen */
        }
      };
      if (signal) signal.addEventListener("abort", cancel, { once: true });
      const decoder = new TextDecoder();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          // Bis zum Zeilenende puffern: eine halbe Zeile ist kein gültiges JSON.
          rest += decoder.decode(value, { stream: true });
          const lines = rest.split("\n");
          rest = lines.pop() ?? "";
          for (const line of lines) await emit(line);
        }
      } finally {
        if (signal) signal.removeEventListener("abort", cancel);
      }
    } else if (typeof res.text === "function") {
      // Kein Strom im Body (z. B. eine Antwort ohne Body): am Stück lesen. Bei
      // einer echten Stream-Antwort käme das nie zurück.
      rest = String(await res.text());
    }
    if (rest.trim()) await emit(rest);
    return { count, messages, nextSince };
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
   * Offene Formulare (native Fragen) einer Session abrufen.
   * Rückgabe: Liste oder null, wenn sie nicht ermittelbar ist. Wirft nur bei
   * HTTP-/Netzfehlern — der Aufrufer behandelt das als „kein Formular“.
   */
  async function fetchOpenForms(sessionID) {
    if (!apiPassword || typeof fetchImpl !== "function") return null;
    const init = {
      method: "GET",
      headers: { Authorization: basicAuth(FORM_API_USER, apiPassword) },
    };
    const signal = timeoutSignal(FORM_API_TIMEOUT_MS);
    if (signal) init.signal = signal;
    const res = await fetchImpl(
      `${apiUrl}/api/session/${encodeURIComponent(sessionID)}/form`,
      init
    );
    const status = Number(res?.status ?? 0);
    if (res?.ok === false || (status && status >= 400)) throw new Error(`HTTP ${status || "?"}`);
    const data = typeof res?.json === "function" ? await res.json() : undefined;
    // Die API antwortet mit { data: [...] }; eine nackte Liste ist auch ok.
    if (Array.isArray(data)) return data;
    if (Array.isArray(data?.data)) return data.data;
    return [];
  }

  /** Antwort in ein Formular eintragen; Rückgabe: die HTTP-Antwort. */
  async function postFormReply(sessionID, formID, answer) {
    const init = {
      method: "POST",
      headers: {
        Authorization: basicAuth(FORM_API_USER, apiPassword),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ answer }),
    };
    const signal = timeoutSignal(FORM_API_TIMEOUT_MS);
    if (signal) init.signal = signal;
    return fetchImpl(
      `${apiUrl}/api/session/${encodeURIComponent(sessionID)}/form/${encodeURIComponent(formID)}/reply`,
      init
    );
  }

  /**
   * Offene Frage abbrechen (Cancel-Endpunkt laut OpenAPI). Der optionale
   * `message`-Query wird bewusst weggelassen: der Text geht anschließend als
   * Prompt in die Session, ein zweiter Zustellweg wäre nur verwirrend.
   */
  async function deleteForm(sessionID, formID) {
    const init = {
      method: "DELETE",
      headers: { Authorization: basicAuth(FORM_API_USER, apiPassword) },
    };
    const signal = timeoutSignal(FORM_API_TIMEOUT_MS);
    if (signal) init.signal = signal;
    return fetchImpl(
      `${apiUrl}/api/session/${encodeURIComponent(sessionID)}/form/${encodeURIComponent(formID)}`,
      init
    );
  }

  /** 204 = angenommen; ein 2xx/ok gilt ebenfalls (tolerant je Version). */
  function formAccepted(res) {
    return res?.status === 204 || res?.ok === true;
  }

  /** Bestätigung, dass die Antwort in der offenen Frage gelandet ist. */
  async function confirmFormReply(text) {
    await publish({
      title: FORM_REPLY_ACK_TITLE,
      message: clampText(text, 200),
      priority: 2,
      tags: ["leftwards_arrow_with_hook"],
    });
  }

  /** Formular-Reply mit Fehlerfang — die Brücke darf nie werfen. */
  async function postFormReplyQuiet(sessionID, formID, answer) {
    try {
      return await postFormReply(sessionID, formID, answer);
    } catch (error) {
      log("Antwort konnte nicht ins Formular eingetragen werden:", error?.message ?? String(error));
      diag("form-reply-fehler", error?.message ?? String(error));
      return null;
    }
  }

  /**
   * Die offenen Frage-Formulare abbrechen. Rückgabe: { ok, formIDs } — ok ist
   * nur true, wenn wirklich jedes Formular geschlossen werden konnte. Fehler
   * werden protokolliert, nicht geworfen.
   */
  async function cancelQuestionForms(sessionID, questions) {
    const formIDs = [];
    let ok = true;
    for (const form of questions) {
      const formID = typeof form?.id === "string" ? form.id : "";
      if (!formID) {
        ok = false;
        diag("form-abbruch-fehler", "Formular ohne ID");
        continue;
      }
      formIDs.push(formID);
      try {
        const res = await deleteForm(sessionID, formID);
        if (!formAccepted(res)) {
          ok = false;
          diag("form-abbruch-fehler", `${formID} HTTP ${res?.status ?? "?"}`);
        }
      } catch (error) {
        ok = false;
        log("Frage konnte nicht abgebrochen werden:", error?.message ?? String(error));
        diag("form-abbruch-fehler", `${formID} ${error?.message ?? String(error)}`);
      }
    }
    return { ok, formIDs };
  }

  /**
   * Versucht, die Antwort auf die eine offene native Frage der Session
   * anzuwenden. Rückgabe: Ergebnisobjekt ("replied" = direkt eingetragen,
   * "cancelled" = Frage abgebrochen, der Aufrufer übergibt den Text als Prompt)
   * oder null — dann übernimmt wie bisher die Prompt-Einspeisung. Wirft nie.
   */
  async function tryFormReply(sessionID, text) {
    if (!formReply) return null;
    let forms;
    try {
      forms = await fetchOpenForms(sessionID);
    } catch (error) {
      log("Offene Formulare nicht abrufbar:", error?.message ?? String(error));
      diag("form-abruf-fehler", error?.message ?? String(error));
      return null;
    }
    if (!Array.isArray(forms) || !forms.length) return null;
    const questions = forms.filter(isQuestionForm);
    if (!questions.length) {
      // Nur Fremdformulare (z. B. Freigabe-Dialoge): nicht anfassen.
      diag("form-uebersprungen", `${forms.length} offene Formulare (keine Frage)`);
      return null;
    }
    if (questions.length > 1) {
      // Mehrdeutig: nicht raten, aber auch nicht offen lassen (Deadlock).
      diag("form-mehrdeutig", `${questions.length} offene Fragen`);
    }

    if (questions.length === 1) {
      const form = questions[0];
      const formID = typeof form?.id === "string" ? form.id : "";
      const plan = formID ? planForForm(form, text) : null;

      // 1. Eindeutiger Treffer (Wert/Label/Index) oder Freitext: eintragen.
      if (plan?.kind === "answer") {
        const res = await postFormReplyQuiet(sessionID, formID, { [plan.key]: plan.value });
        if (formAccepted(res)) {
          await confirmFormReply(text);
          diag("form-reply", `${formID} feld=${plan.key}`);
          return { action: "replied", sessionID, text, formID, field: plan.key, value: plan.value };
        }
        // Abgelehnt (400/404/409 …): wie bisher nicht raten — der Text kommt
        // als Prompt an. Kein Abbruch, die Frage war ja klar zuordenbar.
        diag("form-reply-fehler", `HTTP ${res?.status ?? "?"}`);
        return null;
      }

      // 2. Kein eindeutiger Treffer, aber das Feld akzeptiert Strings: den
      // Rohtext dennoch versuchen — der Agent liest den Satz dann selbst.
      if (plan?.kind === "raw") {
        const res = await postFormReplyQuiet(sessionID, formID, { [plan.key]: text });
        if (formAccepted(res)) {
          await confirmFormReply(text);
          diag("form-reply-rohtext", `${formID} feld=${plan.key}`);
          return { action: "replied", sessionID, text, formID, field: plan.key, value: text };
        }
        diag("form-reply-fehler", `HTTP ${res?.status ?? "?"}`);
      } else {
        diag("form-ohne-treffer", oneLine(form?.title ?? "", 60));
      }
    }

    // 3. Kein eindeutiger Treffer: die Frage darf nicht offen bleiben — sonst
    // blockiert der Turn, bis der Nutzer doch noch im Fenster antwortet
    // (Deadlock). Also abbrechen und den Text danach als Prompt einspeisen.
    const cancel = await cancelQuestionForms(sessionID, questions);
    if (!cancel.ok) return null; // Aufrufer speist trotzdem ein (kein Datenverlust)
    diag("form-abgebrochen", cancel.formIDs.join(","));
    return { action: "cancelled", sessionID, text, formIDs: cancel.formIDs };
  }

  /**
   * Eine Nutzernachricht verarbeiten.
   * Rückgabe: { action: "duplicate" | "skipped" | "ignored" | "injected" |
   *             "replied" | "cancelled" | "no-session", … }
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

    // Ziel der Antwort: vorrangig die Session der letzten Benachrichtigung
    // (lastSessionID wird in send/sendOnce gesetzt, Subagenten sind dort schon
    // gefiltert) — sie ist die präziseste Zuordnung. Nur wenn es keine
    // Benachrichtigung gab, greift die zuletzt gesehene Session als Fallback.
    // Ganz zuletzt die persistierte Zuordnung aus NTFY_STATE_FILE: nach einem
    // Dienst-Neustart ist der In-Memory-Zustand leer — ohne diesen Schritt
    // stand „aktion: no-session“ im Protokoll und die Antwort ging verloren.
    // Sie gilt nur, wenn dieser Prozess die Zuordnung noch nicht selbst
    // geschrieben hat; sonst wäre der Dateiwert nur eine Kopie des ohnehin
    // gültigen Speichers.
    const persistedSession = store.writtenHere(topic, "lastSessionId")
      ? ""
      : store.get(topic, "lastSessionId");
    const sessionID =
      (typeof runtime.lastSessionID === "string" ? runtime.lastSessionID : "") ||
      (typeof runtime.lastActiveSession === "string" ? runtime.lastActiveSession : "") ||
      persistedSession;
    if (!sessionID || typeof client?.session?.prompt !== "function") {
      await hint(text);
      return { action: "no-session", text };
    }
    // Vor der Prompt-Einspeisung prüfen, ob die Antwort in eine offene native
    // Frage der Session gehört: eindeutige Treffer werden direkt eingetragen,
    // ein nicht zuordenbarer Satz bricht die Frage ab (sonst bliebe der Turn
    // blockiert) — siehe tryFormReply. Danach wird der Text immer als Prompt
    // eingespeist; jeder Fehler endet in der gewohnten Prompt-Einspeisung.
    const formResult = await tryFormReply(sessionID, text);
    if (formResult?.action === "replied") return formResult;
    try {
      await client.session.prompt({ sessionID, text });
    } catch (error) {
      log("Antwort konnte nicht eingespeist werden:", error?.message ?? String(error));
      await hint(text);
      return { action: "no-session", text, reason: "prompt-fehler" };
    }
    // Nach dem Abbruch einer Frage bestätigt die Meldung, dass der Text als
    // Nachricht angekommen ist — und dass kein Fenster mehr offen steht.
    if (formResult?.action === "cancelled") {
      await publish({
        title: FORM_CANCELLED_ACK_TITLE,
        message: clampText(text, 200),
        priority: 2,
        tags: ["leftwards_arrow_with_hook"],
      });
      return formResult;
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

  /**
   * Endlosschleife: den Strom lesen, Nachrichten verarbeiten, nach einem
   * Abbruch neu verbinden. Wirft nie heraus.
   */
  async function loop(signal, startSince) {
    let since = String(startSince ?? Math.floor(Date.now() / 1000));
    runtime.replySince = since;
    // Jede Fortschreibung der Leseposition auch in die Zustandsdatei: nach einem
    // Dienst-Neustart setzt die Schleife dort an (keine alten Meldungen erneut).
    const rememberSince = (value) => {
      since = value;
      runtime.replySince = value;
      store.set(topic, { since: value });
    };
    diag("start", `since=${since} topic=${topic}`);
    // Wiederholungen ohne eine einzige Nachricht: damit wächst die Wartezeit vor
    // dem Neuverbinden (1 s → 2 s → … bis 30 s) und die Schleife wird kein
    // Hot-Loop, wenn der Strom sofort wieder endet.
    let failures = 0;
    // Dasselbe für HTTP 429 — nur mit deutlich längeren Schritten (bis 5 min).
    let limits = 0;
    while (!signal?.aborted) {
      let count = 0;
      let error = null;
      try {
        const round = await streamOnce(
          since,
          signal,
          async (message) => {
            if (signal?.aborted) return;
            try {
              const result = await handleMessage(message);
              diag("aktion", `${result?.action ?? "?"}${result?.reason ? ` (${result.reason})` : ""}`);
            } catch (cause) {
              log("Antwort konnte nicht verarbeitet werden:", cause?.message ?? String(cause));
              diag("fehler", `verarbeiten: ${cause?.message ?? String(cause)}`);
            }
          },
          // Zeiger laufend mitschreiben: so setzt auch ein lange offener Strom
          // nach einem Plugin-Reload an der richtigen Stelle an.
          (next) => rememberSince(next)
        );
        if (round.nextSince) rememberSince(round.nextSince);
        count = round.count;
        diag("runde", `${count} neue Nachricht(en), since=${since}`);
      } catch (cause) {
        if (signal?.aborted) break;
        error = cause;
        log("Antwort-Strom abgebrochen:", cause?.message ?? String(cause));
        diag("fehler", `strom: ${cause?.message ?? String(cause)}`);
      }
      if (signal?.aborted) break;
      // Eine Drosselung beendet die Schleife nicht, sie wartet nur lange: eine
      // schnelle Wiederholung würde das Limit weiter hochtreiben.
      if (error?.rateLimited) {
        limits += 1;
        failures = 0;
        const waitMs = Math.min(rateLimitMs * 2 ** (limits - 1), rateLimitMaxMs);
        diag("429", `gedrosselt, warte ${Math.round(waitMs / 1000)} s`);
        await sleep(waitMs, signal);
        continue;
      }
      // Nach einem Strom mit Nachrichten wieder bei der Grundwartezeit anfangen
      // — sonst würde ein abendfüllender Strom die Wartezeit hochschaukeln.
      failures = error || count === 0 ? failures + 1 : 0;
      if (count > 0) limits = 0;
      const waitMs = Math.min(reconnectMs * 2 ** Math.max(0, failures - 1), reconnectMaxMs);
      await sleep(waitMs, signal);
    }
    diag("ende", `since=${since}`);
    // Beim Beenden (teardown) den Stand sofort sichern — die Drosselung darf
    // den letzten Zeiger nicht verschlucken.
    store.flush(true);
  }

  return { shouldIgnore, ignoreReason, handleMessage, streamOnce, loop, url, topic };
}

/**
 * Nutzungsrecht (Lease) an einer laufenden Brücke.
 *
 * stop() zählt nur die eigene Referenz herunter — erst wenn kein Nutzer mehr
 * übrig ist, wird die Schleife wirklich beendet. So kann der Span einer
 * einzelnen Plugin-Instanz die Brücke nicht mit in den Tod nehmen, solange
 * andere Instanzen sie noch benutzen.
 */
function leaseFor(entry, shared) {
  const lease = {
    bridge: entry.bridge,
    controller: entry.controller,
    // true, wenn diese Instanz eine bereits laufende Brücke mitbenutzt.
    shared: Boolean(shared),
    released: false,
    stop() {
      if (lease.released) return; // ein zweites stop() ist folgenlos
      lease.released = true;
      entry.refs -= 1;
      if (entry.refs > 0) return; // andere Instanzen nutzen sie weiter
      entry.stopped = true;
      if (SHARED.bridge === entry) SHARED.bridge = null;
      entry.stop();
    },
  };
  return lease;
}

/**
 * Startet die Antwort-Brücke — je Prozess genau EINMAL.
 *
 * OpenCode V2 lädt das Plugin pro HTTP-Span, im Prozess leben also viele
 * Modul-Instanzen und jede rief hier setup() auf. Jede startete damit ihre
 * eigene Schleife: im Diagnoseprotokoll war das an drei verschiedenen
 * `since`-Token zu sehen (drei Brücken, zusammen bis ~35 Anfragen/Minute) — bis
 * ntfy mit HTTP 429 drosselte.
 *
 * Deshalb liegt die laufende Brücke in SHARED.bridge und wird über einen
 * Referenzzähler geteilt: der erste Start startet sie (refs = 1) und liefert
 * ein Nutzungsrecht zurück, jeder weitere Start zählt nur hoch (refs++) und
 * bekommt ein Recht auf dieselbe Brücke. Dessen stop() zählt herunter; erst der
 * letzte Nutzer beendet die Schleife.
 *
 * Bewusst KEIN dauerhaftes „läuft schon"-Flag: endet die Schleife, räumt sie
 * ihren Eintrag selbst weg, und der nächste Start legt wieder eine an.
 *
 * Rückgabe: { bridge, controller, stop } oder null (abgeschaltet/ohne Token).
 */
export function startReplyBridge(options = {}) {
  const env = options.env ?? process.env;
  const runtime = options.runtime ?? replyRuntime();
  const log = options.log ?? (() => {});

  if (!replyEnabled(env)) return null;
  const config = options.config ?? resolveConfig(env);
  if (!config.token) return null;

  // Läuft schon eine Brücke? Dann nur mitzählen: jede weitere Schleife würde
  // denselben `since`-Zeiger ein zweites Mal abfragen.
  const running = SHARED.bridge;
  if (running && !running.stopped) {
    running.refs += 1;
    return leaseFor(running, true);
  }

  const bridge = createReplyBridge({
    ...options,
    client: options.client ?? options.ctx,
    env,
    runtime,
    log,
    config,
  });
  const controller = options.controller ?? new AbortController();
  runtime.replyAbort = controller;

  // Zeiger aus dem Prozesszustand: ein Neustart setzt dort an, wo die vorherige
  // Schleife aufgehört hat. Mit dem Startwert würde sie alte Nachrichten erneut
  // verarbeiten. Reihenfolge: In-Memory-Zeiger → ausdrücklicher Parameter →
  // persistierte Leseposition aus NTFY_STATE_FILE (sie überlebt auch einen
  // Prozess-Neustart, in dem die beiden anderen Quellen leer sind).
  const store = options.stateStore ?? stateStoreFor(env);
  const persistedSince = store.writtenHere(bridge.topic, "since")
    ? ""
    : store.get(bridge.topic, "since");
  const startSince = runtime.replySince || options.since || persistedSince;

  const entry = {
    bridge,
    controller,
    refs: 1, // dieser Start ist der erste Nutzer
    startedAt: Date.now(),
    stopped: false,
    stop: () => controller.abort(),
  };

  const promise = (async () => {
    try {
      await bridge.loop(controller.signal, startSince);
    } catch (error) {
      if (!controller.signal.aborted) {
        log("Antwort-Brücke beendet:", error?.message ?? String(error));
      }
    }
  })();

  // Das Promise bleibt als erledigt erkennbar (replyPromise wird dann wieder
  // frei), damit ein späteres setup() sicher erneut starten kann — auch wenn
  // die Schleife vorher durch einen Fehler geendet hat.
  const settled = promise
    .catch(() => {})
    .finally(() => {
      if (runtime.replyPromise === settled) runtime.replyPromise = null;
      // Die Schleife ist beendet: den Eintrag nicht als „läuft" stehen lassen,
      // sonst würde der nächste Start nur mitzählen und nie eine Schleife
      // beginnen.
      if (SHARED.bridge === entry) SHARED.bridge = null;
    });
  runtime.replyPromise = settled;
  SHARED.bridge = entry;

  return leaseFor(entry, false);
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
      const notifier = createNtfyNotifier({
        env: { ...merged, NTFY_TOKEN: cfg.token, NTFY_URL: cfg.url, NTFY_TOPIC: cfg.topic },
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
        env: { ...merged, NTFY_TOKEN: cfg.token, NTFY_URL: cfg.url, NTFY_TOPIC: cfg.topic },
        publish: (payload) => notifier.send(payload),
        log: options.log ?? debug,
        ...(options.fetch ? { fetch: options.fetch } : {}),
      });
      if (replyBridge) {
        debug(
          replyBridge.shared
            ? `Antwort-Brücke mitbenutzt (topic ${cfg.topic})`
            : `Antwort-Brücke aktiv (topic ${cfg.topic})`
        );
      }

      const controller = new AbortController();
      // Diagnose des Ereignisstroms in dieselbe Datei wie die Antwort-Brücke
      // (NTFY_REPLY_LOG): so ist im Betrieb sichtbar, ob überhaupt Events
      // ankommen und ob der Strom neu aufgebaut wurde.
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
        controller.abort();
        // Nur die eigene Referenz abzählen: erst der letzte Nutzer beendet die
        // Antwort-Brücke (weitere Instanzen brauchen sie weiter).
        replyBridge?.stop();
      };
    },
  };
}

export default createPluginRuntime();
