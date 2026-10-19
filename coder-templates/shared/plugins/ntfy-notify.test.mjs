// Tests für ntfy-notify.js — laufen ohne OpenCode-Server:
//   node coder-templates/shared/plugins/ntfy-notify.test.mjs
//
// Startet einen lokalen HTTP-Sink, spielt Events ein und prüft, was tatsächlich
// als ntfy-Publish rausgeht (Topic, Titel, Priorität, Body, Click-URL).
// Zusätzlich wird die V2-Anbindung (setup → ctx.event.subscribe) mit einem
// Mock-Context getestet.

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {
  createNtfyNotifier as createNtfyNotifierRaw,
  createPluginRuntime as createPluginRuntimeRaw,
  errorFingerprint,
  isServiceProcess,
  resetSharedState,
  SHARED,
  subpathFromDirectory,
} from "./ntfy-notify.js";

// ── Testisolation ─────────────────────────────────────────────────────────
//
// Vorfall vom 17.10.2026: Tests und Live-Dienst haben sich dieselben Pfade
// geteilt. Im Live-Verzeichnis lagen Testdateien, im echten Topic standen
// Testtexte. Deshalb gilt hier: ALLE Artefakte liegen in einem eigenen
// Temp-Verzeichnis, das nach dem Lauf verschwindet; die Fabriken unten bekommen
// die Testpfade fest untergeschoben, damit auch kein Test mit selbst gebauter
// Env in einen Live-Pfad schreiben kann.

/** Eigenes Verzeichnis für alle Artefakte dieses Laufs. */
const TEST_DIR = mkdtempSync(path.join(os.tmpdir(), "ntfy-notify-test-"));
/** Datei mit Test-Env: es gibt keine echte Env-Datei für die Tests. */
const TEST_ENV_FILE = path.join(TEST_DIR, "opencode.env");
/** Ungültige Adresse: kein Test darf ein echtes Topic ansprechen. */
const TEST_URL_FALLBACK = "https://ntfy-test.invalid";
/** Pfad einer Artefakt-Datei dieses Laufs (Logs, Env-Dateien, Zustand). */
const testPath = (...parts) => path.join(TEST_DIR, ...parts);

/** Env jeder Test-Instanz: Testpfade zuerst, ausdrückliche Werte gewinnen. */
let stateFileSeq = 0;
function testEnv(env = {}) {
  return {
    NTFY_ENV_FILE: TEST_ENV_FILE,
    // Eigene Zustandsdatei je Instanz: die Fehler-Drossel schreibt Zeitstempel
    // dorthin, und kein Test darf einen anderen (oder gar einen Live-Pfad)
    // beeinflussen. Tests, die die Drossel über einen "Neustart" hinweg prüfen,
    // geben denselben Pfad ausdrücklich mit.
    NTFY_STATE_FILE: testPath(`drossel-${++stateFileSeq}.json`),
    ...env,
  };
}

const withTestEnv = (options = {}) => ({ ...options, env: testEnv(options.env) });
const createNtfyNotifier = (options = {}) => createNtfyNotifierRaw(withTestEnv(options));
const createPluginRuntime = (options = {}) => createPluginRuntimeRaw(withTestEnv(options));
// Kein Test spricht einen echten Server an: erlaubt sind nur die lokale
// Schleuse (127.0.0.1/localhost) und die reservierte Test-Domain ".invalid".
const GUARD_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);
const realFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const target = String(input?.url ?? input ?? "");
  let host = "";
  try {
    host = new URL(target).hostname;
  } catch {
    host = "";
  }
  if (host && !GUARD_HOSTS.has(host) && !host.endsWith(".invalid")) {
    throw new Error(`Test-Isolation: echter Netzwerkaufruf an ${host} (${target})`);
  }
  return realFetch(input, init);
};

/** Verzeichnis nach dem Lauf aufräumen — auch bei einem Fehlerabbruch. */
process.on("exit", () => {
  try {
    rmSync(TEST_DIR, { recursive: true, force: true });
  } catch {
    /* Aufräumen darf nie werfen */
  }
});

// Test-Env-Datei anlegen: nur ein Kommentar, also keine echten NTFY_*-Werte —
// ohne diese Datei würde resolveConfig die echte Env-Datei des Dienstes lesen.
writeFileSync(TEST_ENV_FILE, "# Testlauf: bewusst keine NTFY_*-Werte\n");

let passed = 0;
let failed = 0;

function ok(condition, message) {
  if (condition) {
    passed += 1;
    console.log(`  ✓ ${message}`);
  } else {
    failed += 1;
    console.error(`  ✗ ${message}`);
  }
}

function eq(actual, expected, message) {
  ok(
    actual === expected,
    `${message} (erwartet: ${JSON.stringify(expected)}, war: ${JSON.stringify(actual)})`
  );
}

const received = [];

function startSink() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let raw = "";
      req.on("data", (chunk) => (raw += chunk));
      req.on("end", () => {
        let body;
        try {
          body = JSON.parse(raw);
        } catch {
          body = raw;
        }
        received.push({ method: req.method, url: req.url, headers: req.headers, body });
        res.writeHead(200, { "content-type": "application/json" });
        res.end("{}");
      });
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

const sink = await startSink();
const SINK_URL = `http://127.0.0.1:${sink.address().port}`;

const REPO_DIR = "/home/carstenmueller2002/opencode-coder-env";

/**
 * Serverschlüssel der Oberfläche: base64url des Ursprungs der Basis — genau der
 * Wert, den die Web-UI in ihrer Route /server/<schlüssel>/session/<id> erwartet
 * (im Browser gemessen; die frühere Annahme "local" ließ die UI abstürzen).
 */
const serverKeyOf = (base) => Buffer.from(new URL(base).origin).toString("base64url");
/** Basis der Port-App im Container (so leitet linkBase sie ab). */
const UI_BASE = "https://49374--main--infrastructure--carstenmueller2002-coder.mueller-nas.de";
/** Der daraus berechnete Schlüssel der Oberfläche (ohne Padding). */
const UI_KEY = serverKeyOf(UI_BASE);

function info(id, overrides = {}) {
  return {
    id,
    title: "Ablauf prüfen und Bericht schreiben",
    subpath: "workspaces/infrastructure/server-management",
    location: { directory: `${REPO_DIR}/workspaces/infrastructure/server-management` },
    ...overrides,
  };
}

/**
 * Echte V2-Event-Form (empirisch, OpenCode 2.0.29):
 * { id, created, type, durable: { aggregateID }, location: { directory }, data }
 */
function v2(type, data = {}, opts = {}) {
  const sessionID = opts.sessionID ?? SESSION;
  const event = {
    id: `evt_${type}`,
    created: 1792168391528,
    type,
    durable: { aggregateID: sessionID, seq: 1, version: 1 },
    location: {
      directory: opts.directory ?? `${REPO_DIR}/workspaces/infrastructure/server-management`,
    },
    data: { ...(opts.noDataSessionID ? {} : { sessionID }), ...data },
  };
  if (opts.noDurable) delete event.durable;
  if (opts.noLocation) delete event.location;
  return event;
}

/** Eigener Zustand je Test (die echte Implementierung teilt ihn prozessweit). */
function freshState() {
  return {
    meta: new Map(),
    children: new Map(),
    working: new Set(),
    lastText: new Map(),
    lastSent: new Map(),
    // Sessions mit tatsächlich gesendeter Meldung — Grundlage des Ausblendens
    // beim nächsten Nutzer-Turn (NTFY_CLEAR_ON_RESUME, siehe Plugin).
    notified: new Set(),
    errorThrottle: { file: "", loaded: false, sent: new Map() },
  };
}

function makeNotifier(extra = {}) {
  return createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      ...(extra.env ?? {}),
    },
    log: () => {},
    state: extra.state ?? freshState(),
    ...extra,
  });
}

const SESSION = "ses_test0001abcdef";

// ── 1. Prozesserkennung ───────────────────────────────────────────────────
console.log("\nProzesserkennung");
ok(isServiceProcess(["serve", "--service"]), "serve --service ist der Melde-Prozess");
ok(isServiceProcess(["serve"]), "serve wird ebenfalls akzeptiert");
ok(!isServiceProcess(["attach"]), "attach meldet nicht");
ok(!isServiceProcess(["run", "hi"]), "run meldet nicht");
ok(!isServiceProcess(["tui"]), "tui meldet nicht");

// ── 2. Leere Session erzeugt keine Meldung ────────────────────────────────
console.log("\nKeine Meldung ohne Arbeit");
received.length = 0;
{
  const n = makeNotifier();
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 0, "session.idle ohne vorherige Arbeit → keine Nachricht");
}

// ── 3. Fertig-Meldung mit TLDR ────────────────────────────────────────────
console.log("\nFertig-Meldung");
received.length = 0;
{
  const n = makeNotifier();
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({
    type: "message.part.updated",
    properties: {
      sessionID: SESSION,
      part: { type: "text", role: "assistant", text: "Fertig: alle 3 Punkte umgesetzt." },
    },
  });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });

  eq(received.length, 1, "genau eine Nachricht");
  const body = received[0]?.body ?? {};
  eq(body.topic, "ai", "Topic ai");
  eq(body.title, "[server-management] ✅ Fertig — server-management", "Titel mit Projekt");
  eq(body.priority, 3, "Priorität 3");
  ok(String(body.message).includes("Fertig: alle 3 Punkte umgesetzt."), "TLDR ist enthalten");
  ok(String(body.message).includes("Ablauf prüfen"), "Session-Titel ist enthalten");
  eq(
    body.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "Click-URL ist der Session-Deep-Link der Port-App"
  );
  eq((received[0]?.headers ?? {}).authorization, "Bearer tk_test", "Bearer-Token im Header");
}

// ── 4. Zweites idle löst keine zweite Meldung aus ─────────────────────────
console.log("\nDedup");
{
  const n = makeNotifier();
  received.length = 0;
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "idle ohne neue Arbeit → keine zweite Nachricht");
}

// ── 5. Subagent-Sessions werden geschluckt ────────────────────────────────
console.log("\nSubagenten");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: "ses_child0001", parentID: SESSION, title: "Subagent" } },
  });
  n.markWorking("ses_child0001");
  await n.handleEvent({
    type: "session.status",
    properties: { sessionID: "ses_child0001", status: "active" },
  });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: "ses_child0001" } });
  eq(received.length, 0, "Kind-Session meldet nichts");
}

// ── 6. Frage mit Optionen ─────────────────────────────────────────────────
console.log("\nFrage");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent({
    type: "question.asked",
    properties: {
      sessionID: SESSION,
      questions: [
        { question: "Wie soll ich fortfahren?", options: [{ label: "A: deployen" }, { label: "B: warten" }] },
      ],
    },
  });
  const body = received[0]?.body ?? {};
  eq(received.length, 1, "eine Nachricht");
  eq(body.title, "[server-management] ❓ Frage — server-management", "Frage-Titel");
  eq(body.priority, 3, "Priorität 3");
  ok(String(body.message).includes("Wie soll ich fortfahren?"), "Fragetext enthalten");
  ok(String(body.message).includes("A: deployen"), "Option A enthalten");
  ok(String(body.message).includes("B: warten"), "Option B enthalten");

  // Nach der Antwort läuft die Arbeit weiter → idle meldet wieder.
  received.length = 0;
  await n.handleEvent({ type: "question.replied", properties: { sessionID: SESSION } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "nach der Frage kommt die Fertig-Meldung");
}

// ── 7. Freigabe + Fehler ──────────────────────────────────────────────────
console.log("\nFreigabe und Fehler");
{
  const n = makeNotifier({ errorHoldMs: 0 });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent({
    type: "permission.asked",
    properties: {
      sessionID: SESSION,
      permission: { title: "Bash", metadata: { command: "rm -rf /tmp/x" } },
    },
  });
  const perm = received[0]?.body ?? {};
  eq(perm.title, "[server-management] 🔐 Freigabe — server-management", "Freigabe-Titel");
  eq(perm.priority, 3, "Priorität 3");
  ok(String(perm.message).includes("rm -rf /tmp/x"), "Befehl enthalten");

  received.length = 0;
  await n.handleEvent({
    type: "session.error",
    properties: { sessionID: SESSION, error: { message: "Provider nicht erreichbar" } },
  });
  ok(await until(() => received.length >= 1), "die Fehlermeldung kommt nach dem Haltefenster");
  const err = received[0]?.body ?? {};
  eq(err.title, "[server-management] ⚠️ Fehler — server-management", "Fehler-Titel");
  eq(err.priority, 2, "Priorität 2");
  ok(String(err.message).includes("Provider nicht erreichbar"), "Fehlermeldung enthalten");
}

// ── 8. Ohne Token bleibt alles still ──────────────────────────────────────
console.log("\nOhne Token");
{
  const n = makeNotifier({ env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "" } });
  received.length = 0;
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 0, "ohne Token wird nichts gesendet");
}

// ── 9. Unbekannter Session-Pfad fällt sauber zurück ───────────────────────
console.log("\nFallback ohne Workspace-Pfad");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: SESSION, title: "Irgendwas" } },
  });
  await n.handleEvent({ type: "session.status", properties: { sessionID: SESSION, status: "active" } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  const body = received[0]?.body ?? {};
  eq(body.title, "✅ Fertig — opencode", "Projekt-Fallback");
  eq(
    body.click,
    `https://coder.mueller-nas.de/server/${serverKeyOf("https://coder.mueller-nas.de/")}/session/${SESSION}`,
    "URL-Fallback plus Session-Deep-Link"
  );
}

// ── 10. Flache Event-Form (ohne properties) ───────────────────────────────
console.log("\nFlache Event-Form");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", session: info(SESSION) });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", sessionID: SESSION });
  eq(received.length, 1, "auch ohne properties-Wrapper wird gemeldet");
  eq(
    received[0]?.body?.title,
    "[server-management] ✅ Fertig — server-management",
    "Titel aus session-Objekt"
  );
}

// ── 11. V2-Anbindung: setup(subscribe) + prompt-Hook + Cleanup ────────────
console.log("\nV2-Anbindung (setup)");

function makeStream() {
  const queue = [];
  let resolver = null;
  let closed = false;
  const push = (value) => {
    if (closed) return;
    if (resolver) {
      const resolve = resolver;
      resolver = null;
      resolve({ value, done: false });
    } else queue.push(value);
  };
  const close = () => {
    closed = true;
    if (resolver) {
      const resolve = resolver;
      resolver = null;
      resolve({ value: undefined, done: true });
    }
  };
  return {
    push,
    close,
    get closed() {
      return closed;
    },
    iterator: {
      [Symbol.asyncIterator]() {
        return this;
      },
      next() {
        if (queue.length) return Promise.resolve({ value: queue.shift(), done: false });
        if (closed) return Promise.resolve({ value: undefined, done: true });
        return new Promise((resolve) => {
          resolver = resolve;
        });
      },
      return() {
        close();
        return Promise.resolve({ value: undefined, done: true });
      },
    },
  };
}

const wait = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));

{
  resetSharedState();
  received.length = 0;
  const stream = makeStream();
  const hooks = [];
  const ctx = {
    location: { directory: REPO_DIR },
    session: {
      hook: async (name, callback) => {
        hooks.push({ name, callback });
        return { dispose: async () => {} };
      },
    },
    event: {
      subscribe: ({ signal } = {}) => {
        if (signal) signal.addEventListener("abort", () => stream.close());
        return stream.iterator;
      },
    },
  };

  const runtime = createPluginRuntime({
    // Hier geht es nur um Events → Benachrichtigung: alles außer dem Meldetext
    // bleibt außen vor, der Sink ist die einzige Gegenstelle.
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
    },
    argv: ["serve", "--service"],
    log: () => {},
  });
  const cleanup = await runtime.setup(ctx);
  eq(typeof cleanup, "function", "setup liefert eine Cleanup-Funktion");
  eq(hooks.length, 1, "prompt-Hook registriert");
  eq(hooks[0]?.name, "prompt", "Hook heißt 'prompt'");

  // Prompt → markiert die Session als arbeitend (wie im echten Betrieb).
  hooks[0].callback({ sessionID: SESSION });
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  stream.push({
    type: "message.part.updated",
    properties: { sessionID: SESSION, part: { type: "text", text: "Bericht steht." } },
  });
  stream.push({ type: "session.idle", properties: { sessionID: SESSION } });
  await wait();

  eq(received.length, 1, "Event-Stream löst Fertig-Meldung aus");
  ok(String(received[0]?.body?.message ?? "").includes("Bericht steht."), "TLDR aus dem Stream");

  cleanup();
  await wait();
  ok(stream.closed, "Cleanup beendet den Event-Stream");
}

// ── 12. Kein Melden außerhalb des Service-Prozesses ───────────────────────
{
  received.length = 0;
  resetSharedState();
  const stream = makeStream();
  const ctx = {
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: { subscribe: () => stream.iterator },
  };
  const runtime = createPluginRuntime({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    argv: ["tui"],
    log: () => {},
  });
  const cleanup = await runtime.setup(ctx);
  eq(cleanup, undefined, "im TUI-Prozess wird nichts registriert");
  stream.push({ type: "session.idle", properties: { sessionID: SESSION } });
  await wait();
  eq(received.length, 0, "TUI-Prozess sendet keine Benachrichtigungen");
}

// ── 13. Selbsttest-Modus ──────────────────────────────────────────────────
{
  received.length = 0;
  const runtime = createPluginRuntime({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_dummy", NTFY_SELFTEST: "1" },
    argv: ["tui"],
    log: () => {},
  });
  const result = await runtime.setup({
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: { subscribe: () => makeStream().iterator },
  });
  eq(received.length, 1, "Selbsttest sendet genau eine Nachricht");
  ok(String(received[0]?.body?.title ?? "").includes("ntfy-Plugin aktiv"), "Selbsttest-Titel");
  ok(String(received[0]?.body?.message ?? "").includes("Selbsttest"), "Selbsttest-Text");
  eq(received[0]?.body?.tags?.[0], "test_tube", "Selbsttest-Tag");
  eq(result, undefined, "Selbsttest registriert keine Hooks");
}

// ── 14. Geteilter Zustand: ein Event, eine Meldung (mehrere Instanzen) ────
console.log("\nMehrere Plugin-Instanzen");
{
  resetSharedState();
  received.length = 0;
  const a = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    log: () => {},
  });
  const b = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    log: () => {},
  });
  await a.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  a.markWorking(SESSION);
  await a.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  await b.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "zwei Instanzen erzeugen nur eine Nachricht");
}

// ── 15. Fallback über /etc/opencode.env (Service-Env kommt nicht immer an) ─
console.log("\nEnv-Datei-Fallback");
{
  resetSharedState();
  received.length = 0;
  const envFile = testPath("ntfy-notify-test.env");
  await (await import("node:fs/promises")).writeFile(
    envFile,
    'BAO_TOKEN="x"\nNTFY_TOKEN="tk_aus_datei"\nNTFY_URL="' + SINK_URL + '"\n'
  );
  const runtime = createPluginRuntime({
    env: { NTFY_SELFTEST: "1", NTFY_ENV_FILE: envFile },
    argv: ["tui"],
    log: () => {},
  });
  await runtime.setup({
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: { subscribe: () => makeStream().iterator },
  });
  eq(received.length, 1, "Selbsttest nutzt den Token aus der Env-Datei");
  eq(received[0]?.headers?.authorization, "Bearer tk_aus_datei", "Token aus Datei");
  eq(received[0]?.url, "/", "URL aus Datei (Sink)");
}

// ── 15b. Alle NTFY_*-Einstellungen aus der Env-Datei (Session-Link) ───────
console.log("\nEnv-Datei: Session-Link und Serverschlüssel");

/**
 * Plugin-Runtime starten und über den Event-Stream eine Fertig-Meldung
 * auslösen (Session anlegen → Antwort-Text → idle). Rückgabe: Klick-Link.
 */
async function triggerFinish(runtime) {
  const stream = makeStream();
  const cleanup = await runtime.setup({
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: { subscribe: () => stream.iterator },
  });
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  stream.push({
    type: "message.part.updated",
    properties: { sessionID: SESSION, part: { type: "text", text: "Bericht steht." } },
  });
  stream.push({ type: "session.idle", properties: { sessionID: SESSION } });
  ok(await until(() => received.length > 0, 1000), "Fertig-Meldung ist raus");
  const click = String(received[0]?.body?.click ?? "");
  cleanup?.();
  await wait();
  return click;
}

{
  resetSharedState();
  received.length = 0;
  const envFile = testPath("ntfy-notify-test-session.env");
  await (await import("node:fs/promises")).writeFile(
    envFile,
    "NTFY_SESSION_URL=https://beispiel.test/\nNTFY_SERVER_KEY=example.test\n"
  );
  const runtime = createPluginRuntime({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_ENV_FILE: envFile,
    },
    argv: ["serve", "--service"],
    log: () => {},
  });
  const click = await triggerFinish(runtime);
  ok(
    click.startsWith("https://beispiel.test/server/example.test/session/"),
    `Klick-Ziel nutzt NTFY_SESSION_URL und NTFY_SERVER_KEY aus der Env-Datei (war: ${click})`
  );
  eq(click, `https://beispiel.test/server/example.test/session/${SESSION}`, "Klick-Ziel vollständig");
}

// ── 15c. Prozess-Env schlägt die Env-Datei ────────────────────────────────
console.log("\nEnv-Datei: Prozess-Env hat Vorrang");
{
  resetSharedState();
  received.length = 0;
  const envFile = testPath("ntfy-notify-test-priority.env");
  await (await import("node:fs/promises")).writeFile(envFile, "NTFY_SERVER_KEY=aus-datei\n");
  const runtime = createPluginRuntime({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_ENV_FILE: envFile,
      NTFY_SERVER_KEY: "aus-env",
    },
    argv: ["serve", "--service"],
    log: () => {},
  });
  const click = await triggerFinish(runtime);
  ok(click.includes("/server/aus-env/session/"), `Prozess-Env gewinnt (war: ${click})`);
  ok(!click.includes("aus-datei"), "der Wert aus der Datei wird nicht benutzt");
}

// ── 16. Echte V2-Event-Form (OpenCode 2.0.29) ─────────────────────────────
console.log("\nV2-Eventform (durable/location/data)");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("session.step.started", { assistantMessageID: "msg_1", agent: "build" }));
  await n.handleEvent(v2("session.step.streamed", { assistantMessageID: "msg_1" }));
  await n.handleEvent(v2("session.reasoning.delta", { assistantMessageID: "msg_1", ordinal: 0, delta: "hm" }));
  await n.handleEvent(v2("session.text.started", { assistantMessageID: "msg_1", ordinal: 0 }));
  await n.handleEvent(v2("session.text.delta", { assistantMessageID: "msg_1", ordinal: 0, delta: "Alles " }));
  await n.handleEvent(
    v2("session.text.ended", {
      assistantMessageID: "msg_1",
      ordinal: 0,
      text: "Alles erledigt: ntfy-Hub ist aktiv.",
    })
  );
  await n.handleEvent(v2("session.idle", {}));

  eq(received.length, 1, "V2-Folge ergibt genau eine Meldung");
  const body = received[0]?.body ?? {};
  eq(body.title, "[server-management] ✅ Fertig — server-management", "Projekt-Label aus location.directory");
  ok(String(body.message).includes("Alles erledigt"), "TLDR stammt aus session.text.ended");
  eq(
    body.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "Click-URL aus location.directory (mit Session-Pfad)"
  );
}
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("session.step.started", {}, { noDataSessionID: true }));
  await n.handleEvent(v2("session.idle", {}, { noDataSessionID: true }));
  eq(received.length, 1, "Session-ID aus durable.aggregateID genügt");
}

// ── 17. Frage (V2: form.created) ──────────────────────────────────────────
console.log("\nFrage (form.created)");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2("form.created", {
      formID: "frm_1",
      questions: [
        { question: "Weiter mit Phase 4?", options: [{ label: "Ja" }, { label: "Nein" }] },
      ],
    })
  );
  eq(received.length, 1, "form.created meldet");
  const body = received[0]?.body ?? {};
  eq(body.title, "[server-management] ❓ Frage — server-management", "Titel der Frage");
  eq(body.priority, 3, "Priorität 3");
  ok(String(body.message).includes("Weiter mit Phase 4?"), "Fragetext enthalten");
  ok(String(body.message).includes("1) Ja"), "Option 1 enthalten");
  ok(String(body.message).includes("2) Nein"), "Option 2 enthalten");
  ok(/\n2\) Nein/.test(String(body.message)), "Optionen stehen je in eigener Zeile");
}
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("form.created", { form: { schema: "x", nested: { weird: true } } }));
  eq(received.length, 1, "unbekannte Form-Struktur meldet trotzdem");
  ok(String(received[0]?.body?.message ?? "").length > 10, "Fallback-Body ist gefüllt");
}
// Echte Nutzlast (aus dem Event-Stream von 2.0.29 mitgeschrieben)
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2(
      "form.created",
      {
        form: {
          id: "frm_14591f5e9001ag8YmC8x6DmaQs",
          sessionID: SESSION,
          title: "Questions",
          metadata: { kind: "question" },
          fields: [
            {
              key: "q0",
              title: "Hermes-Meldungen",
              description: "Für Hermes: was soll in den Hub gemeldet werden?",
              options: [
                { key: "a", label: "Antworten + Fragen + Fehler" },
                { key: "b", label: "Nur fertige Antworten" },
              ],
            },
          ],
        },
      },
      { noDurable: true, noDataSessionID: true }
    )
  );
  eq(received.length, 1, "echtes form.created (Session in data.form) meldet");
  const body = received[0]?.body ?? {};
  eq(body.title, "[server-management] ❓ Frage — server-management", "Titel trotz fehlender data.sessionID");
  ok(String(body.message).includes("Hermes-Meldungen"), "Feld-Titel enthalten");
  ok(String(body.message).includes("was soll in den Hub gemeldet werden?"), "Beschreibung enthalten");
  ok(String(body.message).includes("Nur fertige Antworten"), "Optionen enthalten");
}

// ── 18. Freigabe und Fehler in V2-Form ────────────────────────────────────
console.log("\nFreigabe und Fehler (V2)");
{
  const n = makeNotifier({ errorHoldMs: 0 });
  received.length = 0;
  await n.handleEvent(
    v2("permission.asked", {
      permission: { title: "Ausführen erlauben", metadata: { command: "sudo systemctl restart x" } },
    })
  );
  eq(received.length, 1, "permission.asked meldet");
  eq(received[0]?.body?.priority, 3, "Freigabe hat Priorität 3");
  ok(String(received[0]?.body?.message ?? "").includes("systemctl restart"), "Kommando im Text");

  received.length = 0;
  await n.handleEvent(v2("session.step.failed", { error: { message: "Provider 500" } }));
  ok(await until(() => received.length >= 1), "session.step.failed kommt nach dem Haltefenster");
  eq(received.length, 1, "session.step.failed meldet");
  eq(received[0]?.body?.priority, 2, "Fehler (AI) hat Priorität 2");
  ok(String(received[0]?.body?.message ?? "").includes("Provider 500"), "Fehlertext enthalten");

  received.length = 0;
  await n.handleEvent(v2("session.tool.failed", { id: "call_1", error: { message: "exit 1" } }));
  eq(received.length, 0, "einzelner Tool-Fehler meldet nicht");
}

// ── 19. Pfad-Hilfe ────────────────────────────────────────────────────────
console.log("\nPfad-Hilfe");
eq(
  subpathFromDirectory("/home/a/opencode-coder-env/workspaces/infra/server-management"),
  "workspaces/infra/server-management",
  "Workspace-Pfad wird erkannt"
);
eq(subpathFromDirectory("/root"), "", "Nicht-Workspace-Pfad ergibt leer");

// ── 20. Turn-Ende in V2 (execution.succeeded / step.ended stop) ───────────
console.log("\nTurn-Ende (V2)");
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("session.execution.started", {}));
  await n.handleEvent(
    v2("session.text.ended", { assistantMessageID: "msg_9", ordinal: 0, text: "Bericht steht." })
  );
  await n.handleEvent(v2("session.execution.succeeded", {}));
  eq(received.length, 1, "execution.succeeded meldet das Turn-Ende");
  eq(received[0]?.body?.title, "[server-management] ✅ Fertig — server-management", "Titel beim Turn-Ende");
  ok(String(received[0]?.body?.message).includes("Bericht steht."), "TLDR beim Turn-Ende");
}
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("session.step.started", {}));
  await n.handleEvent(v2("session.step.ended", { finish: "tool-calls" }));
  eq(received.length, 0, "Zwischenschritt (finish=tool-calls) meldet nicht");
  await n.handleEvent(v2("session.text.ended", { text: "Fertig." }));
  await n.handleEvent(v2("session.step.ended", { finish: "stop" }));
  await n.handleEvent(v2("session.execution.succeeded", {}));
  eq(received.length, 1, "step.ended(stop) + execution.succeeded ergeben eine Meldung");
}
{
  const n = makeNotifier({ errorHoldMs: 0 });
  received.length = 0;
  await n.handleEvent(v2("session.step.started", {}));
  await n.handleEvent(v2("session.execution.failed", { error: { message: "Turn abgebrochen" } }));
  ok(await until(() => received.length >= 1), "execution.failed kommt nach dem Haltefenster");
  eq(received.length, 1, "execution.failed meldet einen Fehler");
  eq(received[0]?.body?.priority, 2, "Fehler (AI) mit Priorität 2");
  ok(String(received[0]?.body?.message).includes("Turn abgebrochen"), "Fehlertext enthalten");
}

// ── Fehlermeldungen: Dämpfung über ERROR_HOLD_MS ──────────────────────────
//
// `session.step.failed` trifft VOR dem Abbruch ein. Damit ein selbst
// abgebrochener Turn oder ein Dienst-Neustart nicht als „⚠️ Fehler" im Topic
// landet, hält das Plugin die Meldung kurz zurück (ERROR_HOLD_MS, in Tests über
// options.errorHoldMs gesetzt): ein Abbruch verwirft sie, weitere Fehlschläge
// derselben Session ergeben zusammen EINE Meldung.
console.log("\nFehlermeldungen (Dämpfung)");
{
  // a) Abbruch im Fenster: die Fehlermeldung wird verworfen — die Diagnose
  //    belegt, dass wirklich der Abbruch-Weg gegriffen hat.
  const logPath = testPath("ntfy-fehler-abbruch.log");
  rmSync(logPath, { force: true });
  writeFileSync(logPath, "");
  const n = makeNotifier({ errorHoldMs: 60, env: { NTFY_REPLY_LOG: logPath } });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent(v2("session.step.failed", { error: { message: "Provider 500" } }));
  eq(received.length, 0, "der Fehlschlag allein geht noch nicht raus");
  await n.handleEvent(v2("session.execution.interrupted", {}));
  ok(
    await until(() => readFileSync(logPath, "utf8").includes("fehler-verworfen")),
    "der Abbruch verwirft die zurückgehaltene Meldung (Diagnose: fehler-verworfen)"
  );
  await wait(150);
  eq(received.length, 0, "es kommt keine Fehlermeldung");
  rmSync(logPath, { force: true });
}
{
  // b) Dienst-Neustart im Fenster: dasselbe.
  const n = makeNotifier({ errorHoldMs: 60 });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent(v2("session.step.failed", { error: { message: "kaputt" } }));
  eq(received.length, 0, "gehalten, solange das Fenster läuft");
  await n.handleEvent(v2("location.shutdown", {}));
  await wait(150);
  eq(received.length, 0, "location.shutdown verwirft die Fehlermeldung");
}
{
  // c) Mehrere Fehlschläge derselben Session → eine Meldung mit Anzahl.
  const n = makeNotifier({ errorHoldMs: 60 });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent(v2("session.step.failed", { error: { message: "erster" } }));
  await n.handleEvent(v2("session.step.failed", { error: { message: "zweiter" } }));
  await n.handleEvent(v2("session.step.failed", { error: { message: "dritter" } }));
  eq(received.length, 0, "im Fenster geht noch nichts raus");
  ok(await until(() => received.length >= 1), "nach dem Fenster kommt die Meldung");
  eq(received.length, 1, "drei Fehlschläge ergeben genau eine Meldung");
  eq(received[0]?.body?.priority, 2, "Priorität 2 bleibt");
  ok(
    String(received[0]?.body?.message ?? "").includes("erster"),
    "der erste Fehlschlag liefert den Text"
  );
  ok(
    String(received[0]?.body?.message ?? "").includes("(3 Fehlschläge zusammengefasst)"),
    "die Meldung nennt die Anzahl der zusammengefassten Fehlschläge"
  );
}
{
  // d) Echter Fehlschlag ohne Abbruch: die Meldung kommt (nur verzögert).
  const n = makeNotifier({ errorHoldMs: 30 });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent(v2("session.execution.failed", { error: { message: "echter Fehler" } }));
  eq(received.length, 0, "gehalten, solange das Fenster läuft");
  ok(await until(() => received.length >= 1), "nach dem Fenster kommt die Meldung");
  eq(received[0]?.body?.title, "[server-management] ⚠️ Fehler — server-management", "Fehler-Titel");
  eq(received[0]?.body?.priority, 2, "Priorität 2");
  ok(String(received[0]?.body?.message ?? "").includes("echter Fehler"), "Fehlertext enthalten");
}
{
  // e) Abbruch-Merkmal im Ereignis selbst: keine Meldung, ohne Folgeereignis.
  const n = makeNotifier({ errorHoldMs: 0 });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent(v2("session.error", { error: { message: "weg" }, aborted: true }));
  await n.handleEvent(v2("session.error", { error: { message: "auch weg" }, reason: "aborted" }));
  await wait(120);
  eq(received.length, 0, "ein Abbruch-Merkmal im Ereignis unterdrückt die Meldung");
}


// ── 20b. Fehler-Drossel: gleicher Fehlertext nur einmal je Fenster ────────
//
// Ein Provider-Limit ("Go usage limit exceeded") schlägt bei jedem weiteren
// Turn wieder zu. Ohne Drossel stünde den ganzen Tag dieselbe Meldung im Topic;
// deshalb meldet das Plugin denselben Fehlertext höchstens einmal je
// NTFY_ERROR_THROTTLE_MS. Der Zustand liegt in NTFY_STATE_FILE und übersteht
// damit einen Neustart des Dienstes. Frage/Freigabe/"Fertig" bleiben sofort.
console.log("\nFehler-Drossel");
{
  const logPath = testPath("ntfy-drossel.log");
  const stateFile = testPath("drossel-verlauf.json");
  rmSync(logPath, { force: true });
  writeFileSync(logPath, "");
  rmSync(stateFile, { force: true });
  const env = {
    NTFY_URL: SINK_URL,
    NTFY_TOKEN: "tk_test",
    NTFY_STATE_FILE: stateFile,
    NTFY_REPLY_LOG: logPath,
    NTFY_ERROR_THROTTLE_MS: String(24 * 60 * 60 * 1000),
  };

  // a) Gleicher Text im Fenster: eine Meldung, die zweite wird unterdrückt.
  //    `fail` wartet nach jedem Fehlschlag kurz: sonst fasst das Plugin mehrere
  //    Fehlschläge zu EINER Meldung zusammen (Dämpfungsfenster) und die Drossel
  //    würde gar nicht einzeln befragt.
  const fail = async (notifier, message) => {
    await notifier.handleEvent(v2("session.error", { error: { message } }));
    await wait(30);
  };
  received.length = 0;
  const n = makeNotifier({ errorHoldMs: 0, cooldownMs: 0, env });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await fail(n, "Go usage limit exceeded");
  eq(received.length, 1, "der erste Fehler wird gemeldet");
  await fail(n, "Go usage limit exceeded");
  eq(received.length, 1, "derselbe Fehlertext wird kein zweites Mal gemeldet");
  ok(
    readFileSync(logPath, "utf8").includes("erneut in ~"),
    "die Drossel ist im Diagnose-Log belegt (fehler-gedrosselt, erneut in ~…)"
  );

  // b) Anderer Fehlertext: kommt weiterhin sofort durch.
  await fail(n, "Provider 500");
  eq(received.length, 2, "ein anderer Fehlertext wird weiterhin gemeldet");
  ok(
    String(received[1]?.body?.message ?? "").includes("Provider 500"),
    "die zweite Meldung trägt den neuen Fehlertext"
  );

  // c) Neustart des Dienstes: neue Instanz, leerer Laufzeitzustand, dieselbe
  //    Zustandsdatei — der Fehler bleibt gedrosselt.
  const afterRestart = makeNotifier({ errorHoldMs: 0, cooldownMs: 0, env });
  await afterRestart.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await fail(afterRestart, "Go usage limit exceeded");
  eq(received.length, 2, "nach dem fiktiven Neustart keine weitere Meldung (Zustand aus der Datei)");
  ok(
    JSON.parse(readFileSync(stateFile, "utf8")).errors?.["go usage limit exceeded"] !== undefined,
    "die Zustandsdatei hält den Fingerabdruck des Fehlers"
  );

  // d) Fenster abgelaufen: wieder eine Meldung — mit der Zahl der Unterdrückungen.
  const short = makeNotifier({
    errorHoldMs: 0,
    cooldownMs: 0,
    env: {
      ...env,
      NTFY_STATE_FILE: testPath("drossel-kurz.json"),
      NTFY_ERROR_THROTTLE_MS: "600",
    },
  });
  received.length = 0;
  await short.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await fail(short, "Kalte Kiste");
  eq(received.length, 1, "kurzes Fenster: die erste Meldung geht raus");
  await fail(short, "Kalte Kiste");
  await fail(short, "Kalte Kiste");
  eq(received.length, 1, "innerhalb des kurzen Fensters kommt nichts nach");
  await wait(700);
  await fail(short, "Kalte Kiste");
  eq(received.length, 2, "nach Ablauf des Fensters wird wieder gemeldet");
  ok(
    String(received[1]?.body?.message ?? "").includes("2× unterdrückt"),
    "die Meldung nennt die unterdrückten Wiederholungen"
  );

  // e) Drossel abgeschaltet: jeder Fehlschlag kommt wieder durch.
  const off = makeNotifier({
    errorHoldMs: 0,
    cooldownMs: 0,
    env: { ...env, NTFY_STATE_FILE: testPath("drossel-aus.json"), NTFY_ERROR_THROTTLE_MS: "0" },
  });
  received.length = 0;
  await off.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await fail(off, "Immer dasselbe");
  await fail(off, "Immer dasselbe");
  eq(received.length, 2, "mit NTFY_ERROR_THROTTLE_MS=0 kommt jede Meldung durch");

  // f) Fingerabdruck: wechselnde Zahlen und Kennungen ergeben denselben Schlüssel.
  eq(
    errorFingerprint("Go  Usage LIMIT exceeded"),
    "go usage limit exceeded",
    "Groß-/Kleinschreibung und Leerzeichen werden normalisiert"
  );
  eq(
    errorFingerprint("rate limit 1200/min fuer ses_ab12cd34ef, Code 4291"),
    errorFingerprint("rate limit 7000/min fuer ses_99ff88ee77, Code 5000"),
    "wechselnde Zahlen und Session-Kennungen ändern den Schlüssel nicht"
  );
  ok(
    errorFingerprint("Go usage limit exceeded") !== errorFingerprint("Provider 500"),
    "unterschiedliche Fehler behalten unterschiedliche Schlüssel"
  );
  rmSync(logPath, { force: true });
}

// ── 21. Subagent-Sessions melden nicht (parentID nur via session.get) ─────
console.log("\nSubagent-Filter");
{
  let calls = 0;
  const client = {
    session: {
      get: async ({ sessionID }) => {
        calls += 1;
        return {
          id: sessionID,
          parentID: "ses_parent0000000000000000",
          title: "Unteraufgabe",
          subpath: "workspaces/infrastructure/server-management",
          location: { directory: `${REPO_DIR}/workspaces/infrastructure/server-management` },
        };
      },
    },
  };
  const n = makeNotifier({ client });
  received.length = 0;
  await n.handleEvent(v2("session.execution.started", {}));
  await n.handleEvent(v2("session.text.ended", { text: "Unteraufgabe fertig" }));
  await n.handleEvent(v2("session.execution.succeeded", {}));
  eq(received.length, 0, "Subagent meldet kein Turn-Ende");
  ok(calls > 0, "parentID wird über session.get nachgeladen");
}
{
  const client = {
    session: {
      get: async ({ sessionID }) => ({
        id: sessionID,
        parentID: "ses_parent0000000000000000",
        title: "Unteraufgabe",
        subpath: "workspaces/infrastructure/server-management",
      }),
    },
  };
  const n = makeNotifier({ client });
  received.length = 0;
  await n.handleEvent(
    v2("form.created", { questions: [{ question: "Weiter?", options: [{ label: "Ja" }] }] })
  );
  eq(received.length, 0, "Subagent-Frage meldet nicht");
  await n.handleEvent(
    v2("permission.asked", { permission: { title: "Bash", metadata: { command: "ls -la" } } })
  );
  eq(received.length, 0, "Subagent-Freigabe meldet nicht");
}
{
  // Gegenprobe: eine Session ohne parentID meldet weiterhin.
  const client = {
    session: {
      get: async ({ sessionID }) => ({
        id: sessionID,
        title: "Hauptsession",
        subpath: "workspaces/infrastructure/server-management",
        location: { directory: `${REPO_DIR}/workspaces/infrastructure/server-management` },
      }),
    },
  };
  const n = makeNotifier({ client });
  received.length = 0;
  await n.handleEvent(v2("session.execution.started", {}));
  await n.handleEvent(v2("session.text.ended", { text: "Hauptsession fertig" }));
  await n.handleEvent(v2("session.execution.succeeded", {}));
  eq(received.length, 1, "echte Session meldet trotz session.get");
  eq(received[0]?.body?.title, "[server-management] ✅ Fertig — server-management", "Titel der Hauptsession");
}

// ── 22. Session-Link (Port-App) und Frage-Optionen je Zeile ──────────────
console.log("\nSession-Link und Frage-Optionen");
{
  // CODER_*-Variablen wie im Container: der Link zeigt auf die Port-App der
  // laufenden `opencode serve`-Instanz (HTTP 303 → OpenCode-Web-UI).
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      CODER_WORKSPACE_NAME: "infrastructure",
      CODER_WORKSPACE_OWNER_NAME: "carstenmueller2002",
      CODER_WORKSPACE_AGENT_NAME: "main",
      CODER_AGENT_URL: "https://coder.mueller-nas.de/",
      NTFY_SESSION_PORT: "49374",
    },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent({
    type: "question.asked",
    properties: {
      sessionID: SESSION,
      questions: [
        {
          question: `Wie soll ich fortfahren? ${"Bitte ausführlich begründen. ".repeat(6)}`,
          options: [
            { label: `A: sofort deployen und danach die Verifikation nachziehen ${"x".repeat(60)}` },
            { label: `B: erst Review abwarten und dann gemeinsam entscheiden ${"y".repeat(60)}` },
            { label: `C: abbrechen und einen neuen Entwurf einreichen ${"z".repeat(60)}` },
          ],
        },
      ],
    },
  });
  eq(received.length, 1, "Frage mit drei Optionen meldet");
  const body = received[0]?.body ?? {};
  const message = String(body.message);
  eq(
    body.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "Klick-Link folgt dem Port-Agent-Workspace-Owner-Muster (plus Session-Pfad)"
  );
  ok(message.includes("1) A: sofort deployen"), "Option 1 im Body");
  ok(message.includes("2) B: erst Review abwarten"), "Option 2 im Body");
  ok(message.includes("3) C: abbrechen und einen neuen Entwurf"), "Option 3 im Body");
  ok(/1\) [^\n]+\n2\) [^\n]+\n3\) /.test(message), "zwischen den Optionen steht ein Zeilenumbruch");
  ok(!message.includes(" | "), "kein ' | ' mehr in der Optionsliste");
  ok(message.includes("Ablauf prüfen"), "Session-Titel bleibt in der Kopfzeile");
  ok(message.length > 500, "Optionsliste wird nicht mehr bei 500 Zeichen abgeschnitten");
  ok(body.actions?.[0]?.url === body.click, "'Session öffnen'-Action zeigt auf denselben Link");
}
{
  // Escape-Hatch: NTFY_SESSION_URL schlägt die automatische Ableitung.
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_SESSION_URL: "https://beispiel.test/session/42",
    },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.handleEvent({
    type: "question.asked",
    properties: {
      sessionID: SESSION,
      questions: [{ question: "Weiter?", options: [{ label: "Ja" }] }],
    },
  });
  eq(received.length, 1, "Frage mit Override meldet");
  eq(
    received[0]?.body?.click,
    `https://beispiel.test/session/42/server/${serverKeyOf("https://beispiel.test/session/42")}/session/${SESSION}`,
    "NTFY_SESSION_URL überschreibt die Basis des Klick-Links"
  );
}

/** Wartet bis `check()` wahr ist (kurze Polls, damit Tests nicht schlafen). */
async function until(check, timeoutMs = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (check()) return true;
    // Eigene Wartezeit: `wait` steht weiter oben und ist hier nicht überall
    // schon initialisiert (dieser Helfer wird auch von früheren Abschnitten
    // benutzt).
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return check();
}


// ── 32. Geteilter Zustand: Dedup über Modul-Instanzen (globalThis) ────────
//
// OpenCode V2 lädt das Plugin pro HTTP-Span — im Prozess leben also mehrere
// Modul-Instanzen mit eigenem Scope. Der Zustand hängt deshalb an globalThis
// (SHARED): dieselbe Fertig-Meldung geht auch dann nur einmal raus, wenn zwei
// Instanzen denselben Ereignisstrom sehen. Beide Notifier benutzen bewusst
// kein eigenes `state` — also genau den geteilten Zustand.
console.log("\nGeteilter Zustand (mehrere Modul-Instanzen)");
{
  resetSharedState();
  received.length = 0;
  const env = { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" };
  const first = createNtfyNotifier({ env, log: () => {} });
  const second = createNtfyNotifier({ env, log: () => {} });
  eq(first._state, second._state, "beide Instanzen teilen dasselbe Zustandsobjekt");

  // Zweite Modul-Instanz (wie beim Laden pro HTTP-Span): eigener Modul-Scope,
  // aber dasselbe Objekt an globalThis — nur dadurch greift die Dedup auch
  // nach einem Plugin-Neuladen.
  const secondModule = await import("./ntfy-notify.js?modul-instanz=2");
  eq(secondModule.SHARED, first._state, "eine zweite Modul-Instanz teilt denselben Zustand");

  // Beide Instanzen sehen denselben Ereignisstrom (je ein Abonnement) — ohne
  // geteilten Zustand würde jede ihre eigene Fertig-Meldung senden.
  const events = [
    v2("session.execution.started", {}),
    v2("session.text.ended", { text: "Bericht steht." }),
    v2("session.execution.succeeded", {}),
  ];
  for (const event of events) await first.handleEvent(event);
  for (const event of events) await second.handleEvent(event);
  eq(received.length, 1, "zwei Instanzen erzeugen nur eine Fertig-Meldung");

  // Auch die zweite Runde bleibt geblockt: die Dedup (lastSent) wirkt über
  // die Instanzen hinweg, nicht nur innerhalb einer.
  received.length = 0;
  first.markWorking(SESSION);
  await second.handleEvent(v2("session.idle", {}));
  eq(received.length, 0, "die zweite Instanz bleibt im Dedup der ersten");
}


// ── 34. Klickziel: Session-Deep-Link der OpenCode-Oberfläche ─────────────
//
// Die Oberfläche hat die Route /server/<serverKey>/session/<id> (Solid-Router
// mit Path-Routing); <serverKey> ist base64url des Ursprungs der Basis (im
// Browser gemessen — "local" ließ die UI abstürzen). Jeder Klick zeigt deshalb
// auf den Deep-Link der Session statt nur auf die Basis: der Schlüssel kommt
// kommt aus NTFY_SERVER_KEY, wenn gesetzt, sonst wird er aus der Basis berechnet;
// die Session-ID wird URL-sicher eingesetzt; ohne Session bleibt es die Basis
// (kein Session-Pfad). UI_BASE/UI_KEY stehen oben bei REPO_DIR.
console.log("\nKlickziel (Session-Deep-Link)");

/** Projekt-Kontext wie im Container — daraus leitet linkBase die Basis ab. */
const UI_ENV = {
  NTFY_URL: SINK_URL,
  NTFY_TOKEN: "tk_test",
  CODER_WORKSPACE_NAME: "infrastructure",
  CODER_WORKSPACE_OWNER_NAME: "carstenmueller2002",
  CODER_WORKSPACE_AGENT_NAME: "main",
  CODER_AGENT_URL: "https://coder.mueller-nas.de/",
  NTFY_SESSION_PORT: "49374",
};

// a) Ohne Override ist der Schlüssel base64url des Ursprungs der Basis — exakt
//    der Wert, den die Oberfläche im Browser erwartet.
{
  const n = makeNotifier({ env: UI_ENV });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Fertig-Meldung geht raus");
  eq(
    n.linkFor(SESSION),
    `${UI_BASE}/server/aHR0cHM6Ly80OTM3NC0tbWFpbi0taW5mcmFzdHJ1Y3R1cmUtLWNhcnN0ZW5tdWVsbGVyMjAwMi1jb2Rlci5tdWVsbGVyLW5hcy5kZQ/session/${SESSION}`,
    "der Schlüssel ist base64url des Ursprungs der Basis (wie im Browser gemessen)"
  );
  ok(!n.linkFor(SESSION).includes("="), "der Schlüssel trägt kein Base64-Padding");
  eq(
    received[0]?.body?.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "das Klickziel ist der Session-Deep-Link"
  );
  eq(
    received[0]?.body?.actions?.[0]?.url,
    received[0]?.body?.click,
    "die Session-Aktion trägt denselben Deep-Link"
  );
}

// b2) NTFY_LINK_HOST steuert die Host-Endung nach dem Owner (inkl. Trennzeichen);
//     leer/ungesetzt bleibt beim bisherigen Default "-<Host der CODER_AGENT_URL>".
{
  const LINK_BASE =
    "https://49374--main--infrastructure--carstenmueller2002.apps.mueller-nas.de";
  const LINK_KEY = serverKeyOf(LINK_BASE);
  const linked = makeNotifier({ env: { ...UI_ENV, NTFY_LINK_HOST: ".apps.mueller-nas.de" } });
  received.length = 0;
  await linked.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  linked.markWorking(SESSION);
  await linked.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Fertig-Meldung mit NTFY_LINK_HOST geht raus");
  eq(
    linked.linkFor(SESSION),
    `${LINK_BASE}/server/${LINK_KEY}/session/${SESSION}`,
    "NTFY_LINK_HOST ergänzt die Endung nach dem Owner (Schlüssel bleibt base64url des Ursprungs)"
  );
  eq(
    received[0]?.body?.click,
    `${LINK_BASE}/server/${LINK_KEY}/session/${SESSION}`,
    "das Klickziel trägt die NTFY_LINK_HOST-Endung"
  );

  const blank = makeNotifier({ env: { ...UI_ENV, NTFY_LINK_HOST: "   " } });
  await blank.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    blank.linkFor(SESSION),
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "ein nur-Whitespace-Wert fällt auf den Standard-Host zurück"
  );

  const empty = makeNotifier({ env: { ...UI_ENV, NTFY_LINK_HOST: "" } });
  await empty.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    empty.linkFor(SESSION),
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "ein leerer Wert fällt auf den Standard-Host zurück"
  );
}

// b) NTFY_SERVER_KEY gewinnt als Override; leer → berechneter Schlüssel.
{
  const sidecar = makeNotifier({ env: { ...UI_ENV, NTFY_SERVER_KEY: "sidecar" } });
  received.length = 0;
  await sidecar.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    sidecar.linkFor(SESSION),
    `${UI_BASE}/server/sidecar/session/${SESSION}`,
    "NTFY_SERVER_KEY='sidecar' steht im Klick-Link"
  );
  sidecar.markWorking(SESSION);
  await sidecar.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung mit eigenem Schlüssel geht raus");
  eq(
    received[0]?.body?.click,
    `${UI_BASE}/server/sidecar/session/${SESSION}`,
    "der Schlüssel steht auch in der Meldung"
  );

  const blank = makeNotifier({ env: { ...UI_ENV, NTFY_SERVER_KEY: "   " } });
  await blank.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    blank.linkFor(SESSION),
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "ein leerer Schlüssel fällt auf den berechneten Schlüssel zurück"
  );
}

// c) Sonderzeichen: Session-ID und Schlüssel werden URL-sicher eingesetzt.
{
  const weird = "ses_a/b c?d";
  const n = makeNotifier({ env: UI_ENV });
  await n.handleEvent({ type: "session.created", properties: { info: info(weird) } });
  eq(
    n.linkFor(weird),
    `${UI_BASE}/server/${UI_KEY}/session/ses_a%2Fb%20c%3Fd`,
    "Sonderzeichen der Session-ID werden kodiert"
  );
  const keyed = makeNotifier({ env: { ...UI_ENV, NTFY_SERVER_KEY: "a/b" } });
  await keyed.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    keyed.linkFor(SESSION),
    `${UI_BASE}/server/a%2Fb/session/${SESSION}`,
    "der Serverschlüssel wird ebenfalls kodiert"
  );
}

// d) Ohne (bzw. mit leerer) Session-ID bleibt die Basis — ohne Session-Pfad.
{
  const n = makeNotifier({ env: { ...UI_ENV, NTFY_FALLBACK_URL: UI_BASE } });
  eq(n.linkFor(""), UI_BASE, "ohne Session-ID bleibt die Basis");
  eq(n.linkFor(undefined), UI_BASE, "eine fehlende Session-ID ergibt die Basis");
  ok(!n.linkFor("   ").includes("/server/"), "nur Leerraum zählt als leere Session-ID");

  const fixed = makeNotifier({
    env: { ...UI_ENV, NTFY_SESSION_URL: "https://beispiel.test/base/" },
  });
  eq(
    fixed.linkFor(SESSION),
    `https://beispiel.test/base/server/${serverKeyOf("https://beispiel.test/base/")}/session/${SESSION}`,
    "die feste Basis wird ohne abschließenden Slash benutzt (Schlüssel aus ihrem Ursprung)"
  );
  eq(fixed.linkFor(""), "https://beispiel.test/base/", "ohne Session-ID bleibt sie unverändert");
}

// e) Eine nicht auswertbare Basis: kein Absturz, kein kaputter Link — es bleibt
//    beim bisherigen Rückfall-Schlüssel "local".
{
  const n = makeNotifier({ env: { ...UI_ENV, NTFY_SESSION_URL: "keine-url" } });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    n.linkFor(SESSION),
    `keine-url/server/local/session/${SESSION}`,
    "ohne auswertbare Basis bleibt der bisherige Rückfall-Schlüssel"
  );
  ok(!n.linkFor(SESSION).includes("undefined"), "der Rückfall-Link ist nicht kaputt");

  received.length = 0;
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht auch mit unauswertbarer Basis raus");
  eq(
    received[0]?.body?.click,
    `keine-url/server/local/session/${SESSION}`,
    "die Meldung trägt den Rückfall-Link (kein Fehler nach außen)"
  );
}


// ── Arbeitsbereichs-Kennung im Titel ("[<name>] …") ──────────────────────
//
// Bei mehreren Arbeitsbereichen soll auf dem Handy sofort erkennbar sein,
// woher eine Meldung kommt: der Name steht als "[<name>] " vor dem Titel. Er
// kommt aus NTFY_WORKSPACE, sonst aus dem letzten Pfadstück des
// Sitzungs-Verzeichnisses; ohne bekannten Namen bleibt der Titel unverändert.
// Klickziel und Aktionen bleiben unberührt.
console.log("\nArbeitsbereich im Titel");
{
  // a) NTFY_WORKSPACE setzt die Kennung.
  const n = makeNotifier({ env: { ...UI_ENV, NTFY_WORKSPACE: "infra" } });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Fertig-Meldung geht raus");
  eq(received[0]?.body?.title, "[infra] ✅ Fertig — server-management", "NTFY_WORKSPACE steht im Titel");
  eq(
    received[0]?.body?.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "das Klickziel bleibt unverändert"
  );
  eq(
    received[0]?.body?.actions?.[0]?.url,
    received[0]?.body?.click,
    "die Aktionen bleiben unverändert"
  );
}
{
  // b) Ohne NTFY_WORKSPACE kommt der Name aus dem Sitzungs-Verzeichnis.
  const n = makeNotifier({ env: UI_ENV });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(
    received[0]?.body?.title,
    "[server-management] ✅ Fertig — server-management",
    "der Name wird aus dem letzten Pfadstück des Verzeichnisses abgeleitet"
  );
  eq(n.projectFor(SESSION), "server-management", "das Projekt-Label im Titel bleibt wie bisher");
}
{
  // c) Ohne bekannten Namen bleibt der Titel unverändert — bei einer Meldung
  //    ohne Session ebenso wie bei einer Session ohne Verzeichnis.
  const n = makeNotifier({ env: UI_ENV });
  received.length = 0;
  await n.send({ title: "⚠️ Antwort ohne Ziel-Session", message: "Text" });
  eq(received[0]?.body?.title, "⚠️ Antwort ohne Ziel-Session", "ohne Session keine Kennung");

  const ohne = makeNotifier();
  received.length = 0;
  await ohne.handleEvent({
    type: "session.created",
    properties: { info: { id: SESSION, title: "Irgendwas" } },
  });
  ohne.markWorking(SESSION);
  await ohne.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received[0]?.body?.title, "✅ Fertig — opencode", "ohne Verzeichnis bleibt der Titel wie bisher");
}
{
  // d) Längenbegrenzung: reicht MAX_TITLE nicht, wird der Name gekürzt — die
  //    Kennung bleibt vorn stehen und der Titel wird nicht überschritten.
  const n = makeNotifier({
    env: { ...UI_ENV, NTFY_WORKSPACE: "ein-sehr-langer-arbeitsbereichsname" },
  });
  received.length = 0;
  await n.send({ title: `✅ Fertig — ${"x".repeat(50)}`, message: "Text" });
  const title = String(received[0]?.body?.title ?? "");
  ok(title.length <= 90, "der Titel bleibt auf MAX_TITLE begrenzt");
  ok(title.startsWith("[ein-sehr-langer"), "die Kennung steht weiterhin vorn");
  ok(title.includes("…"), "bei Platzmangel wird der Name gekürzt");
}

// ── 37. Sessions per Titel stummschalten (NTFY_IGNORE_TITLES) ─────────────
//
// Manche Sessions sind selbst Diagnose (z. B. „Hestia Ausfall-Probe“) und
// sollen keine Meldungen erzeugen. NTFY_IGNORE_TITLES nimmt kommagetrennte
// Teilzeichenketten (getrimmt, Groß-/Kleinschreibung egal): passt eine davon
// auf den Titel der Session, geht gar nichts raus — weder Frage noch Fertig
// noch Fehler. Leer/nicht gesetzt bleibt das Verhalten unverändert.
console.log("\nStummschalten per Titel");
{
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_IGNORE_TITLES: "  ausfall-probe , " },
    errorHoldMs: 0,
  });
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: info(SESSION, { title: "Hestia Ausfall-Probe" }) },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 0, "stummgeschaltete Session → keine Fertig-Meldung");

  await n.handleEvent(
    v2("form.created", {
      questions: [{ question: "Weiter?", options: [{ label: "Ja" }] }],
    })
  );
  eq(received.length, 0, "… auch keine Frage");
  await n.handleEvent(v2("session.error", { error: { message: "kaputt" } }));
  await wait();
  eq(received.length, 0, "… und kein Fehler");
  eq(n._state.lastSent.size, 0, "der Cooldown wird für stumme Sessions nicht angefasst");
}
{
  // Gegenprobe: ein anderer Titel meldet weiterhin; der Vergleich ist
  // case-insensitiv und Teilzeichenketten genügen.
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_IGNORE_TITLES: "AUSFALL-PROBE" },
  });
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: info(SESSION, { title: "Self-hosted Benachrichtigungsdienst" }) },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "ein anderer Titel meldet wie bisher");
  eq(
    received[0]?.body?.title,
    "[server-management] ✅ Fertig — server-management",
    "die Meldung ist die gewohnte"
  );
}
{
  // Ohne NTFY_IGNORE_TITLES wird nichts gefiltert (unverändertes Verhalten).
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: info(SESSION, { title: "Hestia Ausfall-Probe" }) },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "ohne NTFY_IGNORE_TITLES wird auch dieser Titel gemeldet");
}

// ── 37b. Ereignisform 2.0.x: Session-Infos stehen direkt in `data` ───────
//
// OpenCode 2.0.x liefert die Session-Metadaten nicht mehr verschachtelt unter
// properties.info, sondern in data selbst: { sessionID, title, subpath,
// location, parentID }. Vorher hießen Meldungen „Session <id>" mit dem
// Rückfall-Projekt „opencode", und NTFY_IGNORE_TITLES konnte nicht greifen —
// genau so meldeten sich Hestias stündliche Ausfall-Proben. Diese Tests
// benutzen absichtlich Ereignisse OHNE Wurzel-Location, damit nur `data` die
// Metadaten liefert (die frühere Fassung fiel hier auf die Rückfallnamen
// zurück).
console.log("\nEreignisform 2.0.x (Metadaten in data)");
{
  // a) Titel, Arbeitsbereich und Verzeichnis kommen aus data.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2(
      "session.created",
      {
        title: "Ablauf prüfen",
        subpath: "workspaces/infrastructure/server-management",
        location: { directory: `${REPO_DIR}/workspaces/infrastructure/server-management` },
      },
      { noLocation: true }
    )
  );
  n.markWorking(SESSION);
  await n.handleEvent(v2("session.idle", {}, { noLocation: true }));
  eq(received.length, 1, "die Fertig-Meldung geht raus");
  eq(
    received[0]?.body?.title,
    "[server-management] ✅ Fertig — server-management",
    "Projekt und Kennung stammen aus data (vorher: „✅ Fertig — opencode“)"
  );
  ok(
    String(received[0]?.body?.message).includes("Ablauf prüfen"),
    "der Sessiontitel steht jetzt im Meldungskopf"
  );
}
{
  // b) Damit greift NTFY_IGNORE_TITLES auch in dieser Ereignisform.
  const n = makeNotifier({ env: { NTFY_IGNORE_TITLES: "Ausfall-Probe" } });
  received.length = 0;
  await n.handleEvent(
    v2(
      "session.created",
      {
        title: "Hestia Ausfall-Probe",
        subpath: "workspaces/infrastructure/server-management",
        location: { directory: `${REPO_DIR}/workspaces/infrastructure/server-management` },
      },
      { noLocation: true }
    )
  );
  n.markWorking(SESSION);
  await n.handleEvent(v2("session.idle", {}, { noLocation: true }));
  eq(received.length, 0, "die Probe-Session bleibt stumm (Titel kommt nur aus data)");
  eq(n._state.lastSent.size, 0, "der Cooldown wird auch hier nicht angefasst");
}
{
  // c) Ein Subagent (parentID nur in data) meldet nicht — die Erkennung darf
  //    nicht davon abhängen, dass noch eine properties.info-Form ankommt.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2("session.created", { parentID: SESSION, title: "Subagent" }, {
      sessionID: "ses_child0001",
      noLocation: true,
    })
  );
  n.markWorking("ses_child0001");
  await n.handleEvent(v2("session.idle", {}, { sessionID: "ses_child0001", noLocation: true }));
  eq(received.length, 0, "der Subagent meldet nicht");
}
{
  // d) Gegenprobe: eine Prompt-Nutzlast (id + sessionID, aber ohne
  //    Metadaten) wird nicht als Session-Info missverstanden.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2("session.inbox.enqueued", { id: "msg_1", payload: { text: "hallo" } }, { noLocation: true })
  );
  n.markWorking(SESSION);
  await n.handleEvent(v2("session.idle", {}, { noLocation: true }));
  eq(received.length, 1, "die Meldung geht raus");
  eq(
    received[0]?.body?.title,
    "✅ Fertig — opencode",
    "ohne echte Session-Infos bleibt es beim Rückfallnamen"
  );
}

// ── 38. Klickziel-Stil (NTFY_LINK_STYLE) ─────────────────────────────────
//
// Übergangslösung: "base" setzt das Klickziel (click-Feld und die Aktion
// „Session öffnen") auf die Basis, also die Startseite der Oberfläche.
// Standard bleibt "session" = Session-Deep-Link. Die Aktion „Session öffnen"
// folgt demselben Stil wie das click-Feld.
console.log("\nKlickziel-Stil");
{
  const n = makeNotifier({ env: { ...UI_ENV, NTFY_FALLBACK_URL: UI_BASE, NTFY_LINK_STYLE: "base" } });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(n.linkFor(SESSION), UI_BASE, "Stil 'base' → Klickziel mit Session-Kennung ist die Basis");
  eq(n.linkFor(""), UI_BASE, "Stil 'base' → ohne Session-Kennung ebenfalls die Basis");

  received.length = 0;
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  const body = received[0]?.body ?? {};
  eq(body.click, UI_BASE, "das click-Feld zeigt auf die Basis");
  eq(body.actions?.[0]?.label, "OpenCode öffnen", "im Basis-Stil heißt die Aktion 'OpenCode öffnen'");
  eq(body.actions?.[0]?.url, UI_BASE, "'Session öffnen' zeigt auf die Basis");
}
{
  // Standard "session": wie bisher der Deep-Link — mit und ohne Kennung.
  const n = makeNotifier({ env: { ...UI_ENV, NTFY_FALLBACK_URL: UI_BASE } });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  eq(
    n.linkFor(SESSION),
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "Stil 'session' (Default) → Deep-Link"
  );
  eq(n.linkFor(""), UI_BASE, "Stil 'session' → ohne Session-Kennung die Basis");

  received.length = 0;
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(
    received[0]?.body?.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "der Standard bleibt der Deep-Link"
  );
  eq(
    received[0]?.body?.actions?.[0]?.url,
    received[0]?.body?.click,
    "'OpenCode öffnen' trägt denselben Link"
  );
}

// ── 39. Topic je Arbeitsbereich (NTFY_TOPIC_PREFIX, opt-in) ───────────────
//
// Die Ableitung ist bewusst opt-in: ohne NTFY_TOPIC_PREFIX bleibt es exakt bei
// NTFY_TOPIC ("ai") — sonst landeten Meldungen in Topics, die niemand abonniert
// hat. Mit Prefix und bekanntem Arbeitsbereich gilt "<prefix>-<workspace>",
// einheitlich für den Versand (Meldungen, Titel und Diagnosezeile). Ohne
// bekannten Arbeitsbereich greift wieder NTFY_TOPIC.
console.log("\nTopic je Arbeitsbereich");
{
  // a) Ohne Prefix: alles unverändert.
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_WORKSPACE: "server-management",
    },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "ohne Prefix geht die Meldung raus");
  eq(n.topic, "ai", "ohne NTFY_TOPIC_PREFIX bleibt das Topic bei NTFY_TOPIC");
  eq(received[0]?.body?.topic, "ai", "die Meldung geht weiter ins Topic 'ai'");
}
{
  // b) Prefix gesetzt, Arbeitsbereich aus dem Kontext-Verzeichnis (genau so
  //    übergibt createPluginRuntime es): …/workspaces/infrastructure/server-management.
  const n = createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC_PREFIX: "oc",
    },
    directory: `${REPO_DIR}/workspaces/infrastructure/server-management`,
    state: freshState(),
    log: () => {},
  });
  eq(n.topic, "oc-server-management", "Topic ist <prefix>-<workspace>");
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(
    received[0]?.body?.topic,
    "oc-server-management",
    "der Versand nutzt das Topic des Arbeitsbereichs"
  );
  eq(
    received[0]?.body?.title,
    "[server-management] ✅ Fertig — server-management",
    "der Titel bleibt unverändert"
  );
}
{
  // b2) NTFY_WORKSPACE genügt auch ohne passendes Verzeichnis.
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC_PREFIX: "oc",
      NTFY_WORKSPACE: "server-management",
    },
  });
  eq(n.topic, "oc-server-management", "NTFY_WORKSPACE genügt für die Ableitung");
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received[0]?.body?.topic, "oc-server-management", "auch hier geht die Meldung ins Workspace-Topic");
}
{
  // c) Prefix gesetzt, aber Arbeitsbereich unbekannt → Rückfall auf NTFY_TOPIC.
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_TOPIC_PREFIX: "oc",
    },
  });
  eq(n.topic, "ai", "ohne bekannten Arbeitsbereich bleibt es bei NTFY_TOPIC");
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: SESSION, title: "Ohne Pfad" } },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(received[0]?.body?.topic, "ai", "Rückfall auf NTFY_TOPIC");
}
{
  // d) Die Aktiv-Zeile des Plugins nennt das tatsächlich verwendete Topic
  //    (Konsole für diesen einen setup()-Lauf abgefangen).
  resetSharedState();
  received.length = 0;
  const setupLines = [];
  const originalLog = console.log;
  let cleanup;
  console.log = (...args) => setupLines.push(args.join(" "));
  try {
    const runtime = createPluginRuntime({
      env: {
        NTFY_URL: SINK_URL,
        NTFY_TOKEN: "tk_test",
        NTFY_TOPIC: "ai",
        NTFY_TOPIC_PREFIX: "oc",
        NTFY_WORKSPACE: "server-management",
      },
      argv: ["serve", "--service"],
      log: () => {},
    });
    cleanup = await runtime.setup({
      location: { directory: REPO_DIR },
      session: { hook: async () => ({ dispose: async () => {} }) },
      event: { subscribe: () => makeStream().iterator },
    });
  } finally {
    console.log = originalLog;
  }
  const aktiv = setupLines.find((line) => line.includes("[ntfy-notify] aktiv")) ?? "keine Zeile";
  ok(
    aktiv.includes("topic oc-server-management"),
    `die Aktiv-Zeile nennt das tatsächlich verwendete Topic (war: ${aktiv})`
  );
  cleanup?.();
  await wait();
}

// ── 39b. NTFY_TOPIC_NAME: nur das Topic, nie Icon oder Titel ──────────────
//
// Der Dienst läuft im Repo-Wurzelverzeichnis (REPO_DIR) — dessen Pfad enthält
// kein "/workspaces/", also kommt aus dem Verzeichnis kein Arbeitsbereich und
// das Topic bliebe bei NTFY_TOPIC ("ai"). NTFY_TOPIC_NAME setzt genau diesen
// Teil des Topics (zusammen mit NTFY_TOPIC_PREFIX). Icon und Titel-Kennung
// bleiben unangetastet: sie folgen weiter dem Sitzungs-Verzeichnis bzw.
// NTFY_WORKSPACE (sonst überschriebe die neue Variable die bewährten Icons).
console.log("\nTopic-Name (NTFY_TOPIC_NAME)");
{
  // a) Prefix + NTFY_TOPIC_NAME → "<prefix>-<name>", auch ohne Workspace-Pfad.
  const n = createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_TOPIC_PREFIX: "oc",
      NTFY_TOPIC_NAME: "infrastructure",
    },
    directory: REPO_DIR,
    state: freshState(),
    log: () => {},
  });
  eq(n.topic, "oc-infrastructure", "NTFY_TOPIC_NAME ergibt <prefix>-<name>");
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(
    received[0]?.body?.topic,
    "oc-infrastructure",
    "der Versand nutzt das Topic aus NTFY_TOPIC_NAME"
  );
}
{
  // b) Gegenprobe: ohne NTFY_TOPIC_NAME und ohne Workspace-Pfad bleibt es bei
  //    NTFY_TOPIC — genau das Verhalten von heute.
  const n = createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_TOPIC_PREFIX: "oc",
    },
    directory: REPO_DIR,
    state: freshState(),
    log: () => {},
  });
  eq(n.topic, "ai", "ohne NTFY_TOPIC_NAME bleibt das Topic bei NTFY_TOPIC");
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(received[0]?.body?.topic, "ai", "der Versand bleibt im Topic 'ai'");
}
{
  // c) Icon und Titel kommen weiter aus dem Sitzungs-Verzeichnis: derselbe
  //    Ablauf einmal mit, einmal ohne NTFY_TOPIC_NAME — nur das Topic darf
  //    sich unterscheiden. Die Sitzung liegt in homeassistant, nicht in
  //    infrastructure (dem Topic-Namen).
  const iconBase = "https://icons.invalid/base";
  const lauf = async (topicName) => {
    const n = createNtfyNotifier({
      env: {
        NTFY_URL: SINK_URL,
        NTFY_TOKEN: "tk_test",
        NTFY_TOPIC: "ai",
        NTFY_TOPIC_PREFIX: "oc",
        NTFY_ICON_BASE: iconBase,
        ...(topicName ? { NTFY_TOPIC_NAME: topicName } : {}),
      },
      directory: REPO_DIR,
      state: freshState(),
      log: () => {},
    });
    received.length = 0;
    const sesInfo = info(SESSION, {
      location: { directory: `${REPO_DIR}/workspaces/infrastructure/homeassistant` },
    });
    await n.handleEvent({ type: "session.created", properties: { info: sesInfo } });
    n.markWorking(SESSION);
    await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
    return { topic: n.topic, body: received[0]?.body ?? {} };
  };
  const mit = await lauf("infrastructure");
  const ohne = await lauf("");
  eq(mit.topic, "oc-infrastructure", "mit NTFY_TOPIC_NAME folgt das Topic dem Topic-Namen");
  eq(ohne.topic, "ai", "ohne NTFY_TOPIC_NAME bleibt das Topic bei NTFY_TOPIC");
  eq(
    mit.body.icon,
    `${iconBase}/homeassistant.png?v=4`,
    "das icon zeigt weiter auf den Arbeitsbereich des Sitzungs-Verzeichnisses"
  );
  eq(mit.body.icon, ohne.body.icon, "NTFY_TOPIC_NAME ändert das icon-Feld nicht");
  eq(mit.body.title, ohne.body.title, "NTFY_TOPIC_NAME ändert den Titel nicht");
  ok(
    String(mit.body.title ?? "").startsWith("[homeassistant]"),
    `die Titel-Kennung kommt weiter aus dem Sitzungs-Verzeichnis (war: ${mit.body.title})`
  );
}

// ── 40. Symbol je Meldung (NTFY_ICON_BASE) ────────────────────────────────
//
// Ist NTFY_ICON_BASE gesetzt und der Arbeitsbereich bekannt, trägt die Meldung
// das ntfy-Feld `icon` = "<basis>/<workspace>.png?v=4" (JSON-Feld `icon`, als
// Header `Icon`; die Apps zeigen das Bild als Benachrichtigungssymbol). Die
// Versionskennung umgeht den Icon-Cache der Apps (Konstante ICON_VERSION im
// Plugin). Ohne die Basis oder ohne bekannten Arbeitsbereich entfällt das Feld
// — dann bleibt das Verhalten unverändert.
console.log("\nSymbol (icon)");
const ICON_BASE =
  "https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons";
{
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_ICON_BASE: ICON_BASE },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(
    received[0]?.body?.icon,
    `${ICON_BASE}/server-management.png?v=4`,
    "icon zeigt auf das PNG des Arbeitsbereichs (mit Versionskennung)"
  );
}
{
  // NTFY_WORKSPACE genügt, wenn die Session kein Verzeichnis mitbringt; ein
  // abschließender Slash der Basis erzeugt kein doppeltes "//".
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_ICON_BASE: `${ICON_BASE}/`,
      NTFY_WORKSPACE: "server-management",
    },
  });
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: SESSION, title: "Ohne Pfad" } },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung ohne Sitzungs-Verzeichnis geht raus");
  eq(
    received[0]?.body?.icon,
    `${ICON_BASE}/server-management.png?v=4`,
    "ein abschließender Slash der Basis ergibt kein doppeltes //"
  );
}
{
  // Ohne NTFY_ICON_BASE: kein icon-Feld (Verhalten unverändert).
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung aus dem Workspace geht raus");
  ok(
    !Object.prototype.hasOwnProperty.call(received[0]?.body ?? {}, "icon"),
    "ohne NTFY_ICON_BASE trägt die Meldung kein icon-Feld"
  );
}
{
  // Mit NTFY_ICON_BASE, aber ohne bekannten Arbeitsbereich: kein icon-Feld.
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_ICON_BASE: ICON_BASE },
  });
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: SESSION, title: "Ohne Pfad" } },
  });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  ok(
    !Object.prototype.hasOwnProperty.call(received[0]?.body ?? {}, "icon"),
    "ohne bekannten Arbeitsbereich gibt es kein icon-Feld"
  );
}

// ── 42. Ausblenden beim nächsten Nutzer-Turn (Sequenz-ID + clear) ─────────
//
// Jede Meldung einer Session trägt die Sequenz-ID "oc-<sessionID>"
// (X-Sequence-ID): im Client ersetzt eine neue Meldung derselben Session die
// vorherige, statt sich zu stapeln. Die Aktionsknöpfe tragen clear: true —
// Antippen blendet aus. Der prompt-Hook ruft beim nächsten Nutzer-Turn
// clearLastNotification: best-effort PUT <basis>/<topic>/<sequenz>/clear —
// nur für Sessions mit tatsächlich gesendeter Meldung, höchstens einmal je
// Turn, Fehler still; abschaltbar per NTFY_CLEAR_ON_RESUME=0/false.
console.log("\nAusblenden (Sequenz-ID, clear, NTFY_CLEAR_ON_RESUME)");
{
  // a) Die Fertig-Meldung trägt die Sequenz-ID; der Session-Knopf blendet aus.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Fertig-Meldung geht raus");
  eq(
    received[0]?.headers?.["x-sequence-id"],
    `oc-${SESSION}`,
    "die Meldung trägt X-Sequence-ID: oc-<sessionID>"
  );
  eq(received[0]?.body?.actions?.[0]?.clear, true, "die Session-Aktion trägt clear: true");
  eq(n._state.notified.has(SESSION), true, "die Session gilt als benachrichtigt");
}
{
  // b) Mitgegebene view-Aktionen erhalten clear ebenfalls (sofern nicht gesetzt).
  const n = makeNotifier();
  received.length = 0;
  await n.send(
    {
      title: "Probe",
      message: "Probe",
      click: "https://beispiel.test/x",
      actions: [{ action: "view", label: "Extern", url: "https://beispiel.test/y" }],
    },
    SESSION
  );
  eq(received.length, 1, "die Meldung mit eigener Aktion geht raus");
  const actions = received[0]?.body?.actions ?? [];
  eq(actions.length, 2, "beide Aktionen sind vorhanden");
  eq(actions[0]?.clear, true, "die mitgegebene view-Aktion trägt clear: true");
  eq(actions[1]?.clear, true, "die erzeugte Session-Aktion trägt clear: true");
  eq(
    received[0]?.headers?.["x-sequence-id"],
    `oc-${SESSION}`,
    "auch die Nutzlast-Meldung trägt die Sequenz-ID"
  );
}
{
  // c) Der nächste Nutzer-Turn (prompt-Hook: markWorking +
  //    clearLastNotification) blendet die letzte Meldung aus — URL und Auth
  //    wie beim Publish; genau einmal, auch bei wiederholtem Aufruf.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  received.length = 0;
  n.markWorking(SESSION);
  await wait(60);
  eq(received.length, 0, "markWorking allein schickt keinen Clear-Aufruf");
  n.clearLastNotification(SESSION);
  ok(
    await until(() =>
      received.some((r) => r.method === "PUT" && r.url === `/ai/oc-${SESSION}/clear`)
    ),
    "der nächste Nutzer-Turn schickt PUT <topic>/oc-<sessionID>/clear"
  );
  const clear = received.find((r) => r.method === "PUT");
  eq(clear?.headers?.authorization, "Bearer tk_test", "der Clear-Aufruf nutzt denselben Token");
  received.length = 0;
  n.clearLastNotification(SESSION);
  n.clearLastNotification(SESSION);
  await wait(60);
  eq(received.length, 0, "ohne neue Meldung kein zweiter Clear-Aufruf");
}
{
  // d) NTFY_CLEAR_ON_RESUME=0/false: der nächste Nutzer-Turn bleibt still.
  for (const wert of ["0", "false"]) {
    const n = makeNotifier({
      env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_CLEAR_ON_RESUME: wert },
    });
    received.length = 0;
    await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
    n.markWorking(SESSION);
    await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
    eq(received.length, 1, `die Meldung geht auch mit NTFY_CLEAR_ON_RESUME=${wert} raus`);
    received.length = 0;
    n.clearLastNotification(SESSION);
    await wait(60);
    eq(received.length, 0, `mit NTFY_CLEAR_ON_RESUME=${wert} wird nicht ausgeblendet`);
  }
}
{
  // e) Ohne gesendete Meldung (Probe-/stumme Sessions) kein Clear-Aufruf.
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.clearLastNotification(SESSION);
  await wait(60);
  eq(received.length, 0, "ohne gesendete Meldung geht kein Clear-Aufruf raus");
  eq(n._state.notified.size, 0, "die Session gilt nicht als benachrichtigt");
}
{
  // f) Ein fehlgeschlagener Clear-Aufruf bricht nichts und erzeugt keine Meldung.
  let clearAttempts = 0;
  const n = makeNotifier({
    fetch: async (input, init) => {
      if (String(init?.method ?? "") === "PUT") {
        clearAttempts += 1;
        throw new Error("hub weg");
      }
      return globalThis.fetch(input, init);
    },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Fertig-Meldung geht raus");
  received.length = 0;
  n.clearLastNotification(SESSION);
  await wait(60);
  eq(clearAttempts, 1, "der Clear-Aufruf wurde versucht");
  eq(received.length, 0, "ein fehlgeschlagener clear erzeugt keine Ersatzmeldung");
  // Danach arbeitet das Plugin ganz normal weiter:
  await n.handleEvent({
    type: "question.asked",
    properties: {
      sessionID: SESSION,
      questions: [{ question: "Noch da?", options: [{ label: "Ja" }] }],
    },
  });
  eq(received.length, 1, "nach dem fehlgeschlagenen Clear läuft das Plugin normal weiter");
}
{
  // g) V2-Anbindung: der prompt-Hook ruft markWorking UND
  //    clearLastNotification — der nächste Prompt blendet die Meldung aus.
  resetSharedState();
  received.length = 0;
  const stream = makeStream();
  const hooks = [];
  const runtime = createPluginRuntime({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    argv: ["serve", "--service"],
    log: () => {},
  });
  const cleanup = await runtime.setup({
    location: { directory: REPO_DIR },
    session: {
      hook: async (name, callback) => {
        hooks.push({ name, callback });
        return { dispose: async () => {} };
      },
    },
    event: {
      subscribe: ({ signal } = {}) => {
        if (signal) signal.addEventListener("abort", () => stream.close());
        return stream.iterator;
      },
    },
  });
  hooks[0].callback({ sessionID: SESSION });
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  stream.push({
    type: "message.part.updated",
    properties: { sessionID: SESSION, part: { type: "text", text: "Bericht steht." } },
  });
  stream.push({ type: "session.idle", properties: { sessionID: SESSION } });
  ok(await until(() => received.length >= 1, 1000), "die Fertig-Meldung ist raus");
  received.length = 0;
  hooks[0].callback({ sessionID: SESSION }); // der nächste Nutzer-Turn
  ok(
    await until(
      () => received.some((r) => r.method === "PUT" && r.url === `/ai/oc-${SESSION}/clear`),
      1000
    ),
    "der prompt-Hook blendet die letzte Meldung aus (PUT …/clear)"
  );
  cleanup();
  await wait();
}

sink.close();

// ── 41. Testisolation: keine Live-Pfade, kein echtes Netz ─────────────────
//
// Der Vorfall vom 17.10.2026 kam über gemeinsame Pfade von Test und Dienst
// zustande. Diese Prüfung liest den eigenen Testquelltext und schlägt an, wenn
// darin noch der Live-Pfad steht — der Suchtext wird bewusst aus Teilen
// zusammengesetzt, damit er nicht selbst im Quelltext auftaucht.
console.log("\nTestisolation");
{
  const quelle = readFileSync(new URL(import.meta.url), "utf8");
  const livePfad = ["", "tmp", "opencode"].join("/");
  ok(!quelle.includes(livePfad), "der Testquelltext nennt den Live-Pfad des Dienstes nirgends");
  ok(TEST_DIR.includes("ntfy-notify-test-"), "die Artefakte liegen in einem eigenen Temp-Verzeichnis");
  ok(TEST_ENV_FILE.startsWith(TEST_DIR), "die Test-Env-Datei liegt im Temp-Verzeichnis");
  ok(TEST_URL_FALLBACK.endsWith(".invalid"), "die Rückfall-Adresse der Tests ist ungültig");
  ok(!TEST_URL_FALLBACK.includes("mueller-nas"), "kein Test deutet auf den echten Hub");

  // Echte Netzwerkaufrufe lehnt die Schleuse im Testlauf ab.
  let fehler = "";
  try {
    await fetch("https://ntfy.kein-test.example/ai/json");
  } catch (error) {
    fehler = String(error?.message ?? error);
  }
  ok(fehler.includes("Test-Isolation"), "echte Adressen werden im Test abgelehnt");

  // Probe-Meldung über den Testweg: eigener fetch-Ersatz, kein Netz.
  const n = makeNotifier({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ id: "aB3xY9zQk12" }) }),
  });
  eq(n.topic, "ai", "Test-Notifier nutzt wie bisher das Topic ai");
  received.length = 0;
  ok(await n.sendOnce(SESSION, "probe", { title: "Probe", message: "Probe" }), "Probe-Meldung geht raus");
  eq(received.length, 0, "der fetch-Ersatz schreibt nichts in den Sink");
}


console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
