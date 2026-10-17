// Tests für ntfy-notify.js — laufen ohne OpenCode-Server:
//   node coder-templates/shared/plugins/ntfy-notify.test.mjs
//
// Startet einen lokalen HTTP-Sink, spielt Events ein und prüft, was tatsächlich
// als ntfy-Publish rausgeht (Topic, Titel, Priorität, Body, Click-URL).
// Zusätzlich wird die V2-Anbindung (setup → ctx.event.subscribe) mit einem
// Mock-Context getestet.

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {
  createNtfyNotifier as createNtfyNotifierRaw,
  createPluginRuntime as createPluginRuntimeRaw,
  createReplyBridge as createReplyBridgeRaw,
  createStateStore,
  isServiceProcess,
  isValidSinceId,
  parseReplyStream,
  replyRuntime,
  resetSharedState,
  SHARED,
  startReplyBridge as startReplyBridgeRaw,
  subpathFromDirectory,
} from "./ntfy-notify.js";

// ── Testisolation ─────────────────────────────────────────────────────────
//
// Vorfall vom 17.10.2026: Tests und Live-Dienst haben sich dieselben Pfade
// geteilt. Im Live-Verzeichnis lagen Testdateien, im echten Topic standen
// Testtexte, und der Live-Zustand zeigte auf einen Fixture-Wert aus dieser
// Suite (msg_user9) — die Antwort-Brücke hat daraufhin den alten Verlauf als
// Prompt in eine Session gespeist. Deshalb gilt hier: ALLE Artefakte liegen in
// einem eigenen Temp-Verzeichnis, das nach dem Lauf verschwindet; die Fabriken
// unten bekommen die Testpfade fest untergeschoben, damit auch kein Test mit
// selbst gebauter Env in einen Live-Pfad schreiben kann.

/** Eigenes Verzeichnis für alle Artefakte dieses Laufs. */
const TEST_DIR = mkdtempSync(path.join(os.tmpdir(), "ntfy-notify-test-"));
/** Zustandsdatei aller Tests — nie der Default des Plugins. */
const TEST_STATE_FILE = path.join(TEST_DIR, "ntfy-state.json");
/** Datei mit Test-Env: es gibt keine echte Env-Datei für die Tests. */
const TEST_ENV_FILE = path.join(TEST_DIR, "opencode.env");
/** Ungültige Adresse: kein Test darf ein echtes Topic ansprechen. */
const TEST_URL_FALLBACK = "https://ntfy-test.invalid";
/** Pfad einer Artefakt-Datei dieses Laufs (Logs, Env-Dateien, Zustand). */
const testPath = (...parts) => path.join(TEST_DIR, ...parts);

/** Env jeder Test-Instanz: Testpfade zuerst, ausdrückliche Werte gewinnen. */
function testEnv(env = {}) {
  return {
    NTFY_STATE_FILE: TEST_STATE_FILE,
    NTFY_ENV_FILE: TEST_ENV_FILE,
    ...env,
  };
}

const withTestEnv = (options = {}) => ({ ...options, env: testEnv(options.env) });
const createNtfyNotifier = (options = {}) => createNtfyNotifierRaw(withTestEnv(options));
const createPluginRuntime = (options = {}) => createPluginRuntimeRaw(withTestEnv(options));
const createReplyBridge = (options = {}) => createReplyBridgeRaw(withTestEnv(options));
const startReplyBridge = (options = {}) => startReplyBridgeRaw(withTestEnv(options));

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
function freshState() {  return {
    meta: new Map(),
    children: new Map(),
    working: new Set(),
    lastText: new Map(),
    lastSent: new Map(),
  };
}

function makeNotifier(extra = {}) {
  return createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      // Kopplungs-Button standardmäßig aus: diese Tests prüfen einzelne
      // Meldungen und dürfen dafür keine echte lokale API anfragen. Der
      // Kopplungs-Test unten schaltet ihn gezielt ein (gemocktes fetch).
      NTFY_PAIR_BUTTON: "0",
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
  eq(body.priority, 4, "Priorität 4");
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
  const n = makeNotifier();
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
  eq(perm.priority, 4, "Priorität 4");
  ok(String(perm.message).includes("rm -rf /tmp/x"), "Befehl enthalten");

  received.length = 0;
  await n.handleEvent({
    type: "session.error",
    properties: { sessionID: SESSION, error: { message: "Provider nicht erreichbar" } },
  });
  const err = received[0]?.body ?? {};
  eq(err.title, "[server-management] ⚠️ Fehler — server-management", "Fehler-Titel");
  eq(err.priority, 5, "Priorität 5");
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
    // NTFY_REPLY_ENABLED=0: hier geht es nur um Events → Benachrichtigung; die
    // Antwort-Brücke hat eigene Tests und würde sonst den Sink abfragen.
    // NTFY_PAIR_BUTTON=0: kein Kopplungslink, dieser Test hat kein fetch-Mock.
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_REPLY_ENABLED: "0",
      NTFY_PAIR_BUTTON: "0",
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" },
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_dummy", NTFY_SELFTEST: "1", NTFY_PAIR_BUTTON: "0" },
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" },
    log: () => {},
  });
  const b = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" },
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
    env: { NTFY_SELFTEST: "1", NTFY_ENV_FILE: envFile, NTFY_PAIR_BUTTON: "0" },
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
    // NTFY_REPLY_ENABLED=0: hier geht es nur um die Event-Meldung; sonst würde
    // die Antwort-Brücke zusätzlich den Sink abfragen (kein Netz im Test).
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_ENV_FILE: envFile,
      NTFY_PAIR_BUTTON: "0",
      NTFY_REPLY_ENABLED: "0",
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
      NTFY_PAIR_BUTTON: "0",
      NTFY_REPLY_ENABLED: "0",
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
  eq(body.priority, 4, "Priorität 4");
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
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2("permission.asked", {
      permission: { title: "Ausführen erlauben", metadata: { command: "sudo systemctl restart x" } },
    })
  );
  eq(received.length, 1, "permission.asked meldet");
  eq(received[0]?.body?.priority, 4, "Freigabe hat Priorität 4");
  ok(String(received[0]?.body?.message ?? "").includes("systemctl restart"), "Kommando im Text");

  received.length = 0;
  await n.handleEvent(v2("session.step.failed", { error: { message: "Provider 500" } }));
  eq(received.length, 1, "session.step.failed meldet");
  eq(received[0]?.body?.priority, 5, "Fehler hat Priorität 5");
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
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("session.step.started", {}));
  await n.handleEvent(v2("session.execution.failed", { error: { message: "Turn abgebrochen" } }));
  eq(received.length, 1, "execution.failed meldet einen Fehler");
  eq(received[0]?.body?.priority, 5, "Fehler mit Priorität 5");
  ok(String(received[0]?.body?.message).includes("Turn abgebrochen"), "Fehlertext enthalten");
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

// ── 23. Antwort-Brücke: reine Logik (ohne Netzwerk) ──────────────────────
console.log("\nAntwort-Brücke (Logik)");

/** Wartet bis `check()` wahr ist (kurze Polls, damit Tests nicht schlafen). */
async function until(check, timeoutMs = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (check()) return true;
    await wait(20);
  }
  return check();
}

{
  const prompts = [];
  const published = [];
  const runtime = { sentIds: ["msg_eigen1"], lastSessionID: "" };
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    // Ohne API-Passwort bleibt der Formular-Weg inaktiv: dieser Test prüft die
    // Prompt-Brücke und darf dafür nie die lokale API anfassen.
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async (payload) => {
      published.push(payload);
      return true;
    },
    log: () => {},
  });

  // a) Eigene Nachricht, erkannt an der Message-ID.
  const byId = await bridge.handleMessage({
    id: "msg_eigen1",
    title: "Nutzertext",
    message: "selbst gesendet",
  });
  eq(byId.action, "skipped", "eigene Nachricht (ID in sentIds) → skipped");
  eq(prompts.length, 0, "eigene Nachricht löst keinen Prompt aus");
  ok(bridge.shouldIgnore({ id: "msg_eigen1" }), "shouldIgnore erkennt die eigene ID");
  ok(!bridge.shouldIgnore({ id: "msg_fremd", title: "Antwort" }), "fremde ID wird nicht ignoriert");

  // b) Eigene Nachricht, erkannt am Titel-Präfix.
  const byTitle = await bridge.handleMessage({
    id: "msg_fremd0",
    title: "✅ Fertig — server-management",
    message: "Bin fertig.",
  });
  eq(byTitle.action, "ignored", "eigene Nachricht (Titel-Präfix) → ignored");
  ok(bridge.shouldIgnore({ title: "⚠️ Fehler — x" }), "shouldIgnore erkennt eigene Titel");
  ok(bridge.shouldIgnore({ title: "🔐 Freigabe — x" }), "shouldIgnore erkennt das Freigabe-Präfix");
  eq(prompts.length, 0, "auch die Titel-Variante löst keinen Prompt aus");

  // c) Leere Nachricht.
  runtime.lastSessionID = SESSION;
  const empty = await bridge.handleMessage({ id: "msg_leer", message: "   " });
  eq(empty.action, "skipped", "leere Nachricht wird übersprungen");

  // d) Fremde Nachricht mit Zielsession → Prompt + Ack.
  published.length = 0;
  const injected = await bridge.handleMessage({
    id: "msg_user1",
    title: "Antwort",
    message: "  Bitte Variante B umsetzen  ",
  });
  eq(injected.action, "injected", "fremde Nachricht wird eingespeist");
  eq(injected.sessionID, SESSION, "Ziel ist die zuletzt benachrichtigte Session");
  eq(prompts.length, 1, "genau ein Prompt");
  eq(prompts[0]?.sessionID, SESSION, "Prompt geht an die Zielsession");
  eq(prompts[0]?.text, "Bitte Variante B umsetzen", "Text wird getrimmt übergeben");
  eq(published.length, 1, "Ack wird veröffentlicht");
  ok(String(published[0]?.title).startsWith("↩️"), "Ack trägt das Antwort-Präfix");
  eq(published[0]?.priority, 2, "Ack ist leise (Priorität 2)");

  // e) Sehr lange Nachricht wird gekürzt.
  const long = await bridge.handleMessage({ id: "msg_lang", message: "A".repeat(5000) });
  eq(long.action, "injected", "lange Nachricht wird eingespeist");
  ok(long.text.length <= 2000, "Text wird auf ~2000 Zeichen gekürzt");
  eq(prompts[1]?.text, long.text, "gekürzter Text geht in den Prompt");

  // f) Fremde Nachricht ohne Zielsession → Hinweis, keine Injektion.
  published.length = 0;
  runtime.lastSessionID = "";
  const orphan = await bridge.handleMessage({ id: "msg_user2", message: "Hallo?" });
  eq(orphan.action, "no-session", "ohne Zielsession wird nichts eingespeist");
  eq(prompts.length, 2, "kein weiterer Prompt");
  eq(published.length, 1, "stattdessen kommt ein Hinweis");
  ok(String(published[0]?.title).includes("ohne Ziel-Session"), "Hinweis nennt die fehlende Session");
  eq(published[0]?.priority, 2, "Hinweis ist leise (Priorität 2)");
}

// ── 24. Antwort-Brücke: NDJSON-Strom (fetch gemockt) ─────────────────────
console.log("\nAntwort-Brücke (Strom)");

/**
 * Gefälschte Antwort mit echtem Strom-Body: die Chunks werden als NDJSON
 * eingespielt und der Strom danach geschlossen — so prüfen die Tests das
 * inkrementelle, zeilenweise Lesen (auch über Chunk-Grenzen hinweg).
 */
function jsonStreamResponse(chunks, status = 200) {
  if (status >= 400) {
    return Promise.resolve({ ok: false, status, body: null, text: async () => "" });
  }
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return Promise.resolve({ ok: true, status, body });
}

/** fetch-Ersatz, der jede Anfrage protokolliert und den Strom danach schließt. */
function streamFetch(calls, chunks, status = 200) {
  return (input, init = {}) => {
    calls.push({ url: String(input), init, done: true, status });
    return jsonStreamResponse(chunks, status);
  };
}

/**
 * fetch-Ersatz mit offenem Strom: die Zeilen kommen sofort, die Verbindung
 * endet erst beim Abbruch (so wie der echte ntfy-Stream mit keepalive-Zeilen).
 */
function openStreamFetch(calls, chunks = []) {
  return (input, init = {}) => {
    const entry = { url: String(input), init, done: false, status: 200 };
    calls.push(entry);
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        const finish = () => {
          entry.done = true;
          try {
            controller.close();
          } catch {
            /* Strom war schon geschlossen */
          }
        };
        if (init.signal?.aborted) finish();
        else init.signal?.addEventListener("abort", finish, { once: true });
      },
      // Die Brücke bricht den wartenden read() über reader.cancel() ab — hier
      // als „Verbindung ist zu“ sichtbar.
      cancel() {
        entry.done = true;
      },
    });
    return Promise.resolve({ ok: true, status: 200, body });
  };
}

{
  const requests = [];
  const runtime = { sentIds: [], lastSessionID: SESSION };
  // Zeilenweise NDJSON; die erste Nachricht kommt absichtlich über eine
  // Chunk-Grenze hinweg — eine halbe Zeile ist kein gültiges JSON und darf erst
  // nach dem Zeilenende ausgewertet werden.
  let calls = 0;
  const fetchStub = (input, init = {}) => {
    calls += 1;
    // Nur die erste Verbindung liefert Zeilen; die zweite steht für einen
    // Strom, in dem gerade nichts anliegt.
    return streamFetch(requests, calls === 1 ? [
      '{"id":"msg_keep","event":"keepalive"}\n{"id":"msg_a1","event":"message","tit',
      'le":"Antwort","message":"Weiter mit B"}\nkein json\n',
      '{"id":"msg_a2","event":"message","title":"Nachtrag","message":"Und danach C"}\n',
    ] : [])(input, init);
  };
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    runtime,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    log: () => {},
  });

  const round = await bridge.streamOnce("1700000000");
  eq(round.count, 2, "NDJSON wird zeilenweise gelesen (Nicht-JSON übersprungen)");
  eq(round.messages.length, 2, "beide Meldungen kommen aus dem Strom an");
  eq(round.messages[0]?.id, "msg_a1", "die über die Chunk-Grenze geteilte Zeile kommt heil an");
  eq(round.messages[1]?.message, "Und danach C", "auch die zweite Meldung wird gelesen");
  eq(round.nextSince, "msg_a2", "since zeigt danach auf die letzte Message-ID");
  ok(String(requests[0]?.url).includes("/ai/json?since=1700000000"), "der Stream wird mit since angefragt");
  ok(!String(requests[0]?.url).includes("poll=1"), "kein Kurzpoll mehr");
  eq(requests[0]?.init?.headers?.Authorization, "Bearer tk_test", "Bearer-Token bei der Abfrage");

  // Keepalive/open-Zeilen tragen keine Nachricht.
  const parsed = parseReplyStream('{"id":"msg_open","event":"open"}\n\n');
  eq(parsed.messages.length, 0, "open-Zeilen ergeben keine Nachricht");
  eq(parsed.nextSince, "msg_open", "die ID wird trotzdem übernommen");

  // Leerer Strom lässt `since` stehen — sonst gäbe es Endlos-Wiederholungen.
  const emptyRound = await bridge.streamOnce("msg_a2", undefined, null, null);
  eq(emptyRound.messages.length, 0, "ein leerer Strom liefert keine Nachrichten");
  eq(emptyRound.nextSince, "msg_a2", "since bleibt bei einem leeren Strom stehen");
}

// ── 25. Antwort-Brücke: genau eine je Prozess (Referenzzählung) ──────────
console.log("\nAntwort-Brücke (Start)");

{
  resetSharedState();
  const runtime = replyRuntime();
  const client = { session: { prompt: async () => {} } };
  const base = (extra = {}) => ({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
    },
    runtime,
    client,
    publish: async () => true,
    log: () => {},
    ...extra,
  });

  const off = startReplyBridge(
    base({ env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_REPLY_ENABLED: "0" } })
  );
  eq(off, null, "NTFY_REPLY_ENABLED=0 startet keine Brücke");
  eq(runtime.replyAbort, null, "ohne Start läuft auch kein Controller");
  eq(SHARED.bridge, null, "ohne Start gibt es auch keinen Brückeneintrag");

  const calls = [];
  const first = startReplyBridge(base({ fetch: openStreamFetch(calls) }));
  ok(first !== null && typeof first.stop === "function", "Brücke startet und liefert stop()");
  eq(first.shared, false, "der erste Start legt die Brücke an");
  eq(runtime.replyAbort, first.controller, "der Start setzt seinen Controller als aktuellen");
  eq(SHARED.bridge?.refs, 1, "der Eintrag zählt den ersten Nutzer");
  ok(await until(() => calls.length === 1), "die Schleife öffnet genau einen Strom");

  // Zweite Instanz (Plugin pro HTTP-Span): sie startet KEINE zweite Schleife,
  // sondern benutzt die laufende Brücke mit.
  const second = startReplyBridge(base({ fetch: openStreamFetch(calls) }));
  ok(second !== null, "ein zweiter Start ist möglich (kein dauerhaftes „läuft schon“)");
  eq(second.shared, true, "der zweite Start benutzt die laufende Brücke");
  eq(second.bridge, first.bridge, "beide Instanzen teilen dasselbe Brückenobjekt");
  eq(second.controller, first.controller, "beide Instanzen teilen denselben Controller");
  eq(SHARED.bridge.refs, 2, "der zweite Start zählt nur hoch");
  eq(calls.length, 1, "es läuft weiterhin genau eine Stromverbindung");
  eq(first.controller.signal.aborted, false, "der zweite Start bricht die laufende Schleife nicht ab");

  // Cleanup der ersten Instanz darf die Brücke nicht mitnehmen.
  first.stop();
  eq(second.controller.signal.aborted, false, "Cleanup der ersten Instanz tötet die Brücke nicht");
  eq(SHARED.bridge.refs, 1, "stop() zählt nur die eigene Referenz herunter");
  eq(calls.filter((call) => !call.done).length, 1, "die Schleife läuft weiter");

  // Dritte Instanz übernimmt, die zweite geht — die Brücke lebt weiter.
  const third = startReplyBridge(base({ fetch: openStreamFetch(calls) }));
  second.stop();
  eq(SHARED.bridge.refs, 1, "die dritte Instanz hält die Brücke am Leben");
  eq(third.controller.signal.aborted, false, "die Brücke läuft weiter, obwohl zwei Instanzen weg sind");
  eq(calls.length, 1, "weiterhin nur eine Verbindung");

  // Erst der letzte Nutzer beendet die Schleife.
  third.stop();
  eq(third.controller.signal.aborted, true, "der letzte Nutzer beendet die laufende Schleife");
  ok(await until(() => SHARED.bridge === null), "der Eintrag ist danach frei");
  ok(
    await until(() => runtime.replyPromise === null),
    "der Schleifen-Promise ist erledigt (Neustart möglich)"
  );
  ok(await until(() => calls.every((call) => call.done)), "die Stromverbindung ist geschlossen");

  // Ein späterer setup() startet wieder — kein „läuft schon“-Zustand mehr.
  const later = startReplyBridge(base({ fetch: openStreamFetch(calls) }));
  ok(later !== null, "nach dem Ende startet ein späterer setup() erneut");
  eq(later.shared, false, "und legt wieder eine frische Brücke an");
  eq(SHARED.bridge.refs, 1, "der Zähler beginnt wieder bei 1");
  later.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 25b. Genau eine Brücke, auch über mehrere setup()-Aufrufe ────────────
//
// Der Live-Befund: OpenCode V2 lädt das Plugin pro HTTP-Span, jedes setup()
// startete seine eigene Brücke — im Diagnoseprotokoll standen drei `start:`-
// Zeilen mit drei verschiedenen `since=`-Token. Hier laden zwei Runtimes im
// selben Prozess (wie zwei Spans): es darf genau ein Brücken-Start sein, und
// nach teardown() aller Nutzer muss sie stehen.
console.log("\nAntwort-Brücke (mehrere setup()-Aufrufe)");
{
  resetSharedState();
  const logPath = testPath("ntfy-reply-bridge-single.log");
  rmSync(logPath, { force: true });
  const readLog = () => {
    try {
      return readFileSync(logPath, "utf8");
    } catch {
      return "";
    }
  };
  const starts = () => readLog().split("\n").filter((line) => line.includes(" start: since=")).length;
  const env = {
    NTFY_URL: SINK_URL,
    NTFY_TOKEN: "tk_test",
    NTFY_PAIR_BUTTON: "0",
    NTFY_REPLY_LOG: logPath,
  };
  const calls = [];
  const fetchStub = openStreamFetch(calls, ['{"id":"msg_open1","event":"keepalive"}\n']);
  const makeCtx = () => {
    const stream = makeStream();
    return {
      location: { directory: REPO_DIR },
      session: { hook: async () => ({ dispose: async () => {} }), prompt: async () => {} },
      event: {
        subscribe: ({ signal } = {}) => {
          if (signal) signal.addEventListener("abort", () => stream.close());
          return stream.iterator;
        },
      },
    };
  };
  const first = createPluginRuntime({
    env,
    argv: ["serve", "--service"],
    fetch: fetchStub,
    log: () => {},
  });
  const second = createPluginRuntime({
    env,
    argv: ["serve", "--service"],
    fetch: fetchStub,
    log: () => {},
  });

  const cleanup1 = await first.setup(makeCtx());
  ok(await until(() => calls.length === 1), "die erste Instanz öffnet den Strom");
  ok(await until(() => starts() === 1), "genau ein Brücken-Start im Diagnoseprotokoll");

  const cleanup2 = await second.setup(makeCtx());
  await wait(50);
  eq(calls.length, 1, "die zweite Instanz startet keine zweite Brücke");
  eq(starts(), 1, "auch im Protokoll bleibt es bei einem Start");
  eq(SHARED.bridge?.refs, 2, "beide Instanzen teilen sich die eine Brücke");

  cleanup1?.();
  await wait(30);
  eq(calls.length, 1, "der erste teardown() lässt die Brücke weiterlaufen");
  eq(SHARED.bridge?.refs, 1, "nur die eigene Referenz wird abgezählt");

  cleanup2?.();
  ok(await until(() => SHARED.bridge === null), "nach teardown() aller Nutzer ist sie gestoppt");
  ok(await until(() => calls.every((call) => call.done)), "die Stromverbindung ist geschlossen");
  eq(starts(), 1, "es blieb bei einem einzigen Brücken-Start");
  resetSharedState();
  rmSync(logPath, { force: true });
}

// ── 26. Antwort-Brücke: since-Zeiger überlebt den Neustart ───────────────
console.log("\nAntwort-Brücke (Neustart)");
{
  resetSharedState();
  const runtime = replyRuntime();
  const calls = [];
  const urls = () => calls.map((call) => decodeURIComponent(String(call.url)));
  // keepalive-Zeile mit ID: der Zeiger rückt weiter, ohne dass eine
  // Nutzernachricht verarbeitet werden muss. Der Strom endet danach — die
  // Wiederverbindung ist hier gerade erwünscht (siehe 27b).
  const fetchStub = streamFetch(calls, ['{"id":"msg_keep9","event":"keepalive"}\n']);
  const base = {
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    reconnectMs: 20,
    log: () => {},
  };

  const first = startReplyBridge({ ...base, since: "1700000000" });
  ok(await until(() => urls().length >= 1), "die erste Schleife verbindet sich");
  ok(urls()[0].includes("since=1700000000"), "der Startwert wird für die erste Verbindung benutzt");
  ok(await until(() => runtime.replySince === "msg_keep9"), "der since-Zeiger wird fortgeschrieben");
  first.stop();
  await wait();
  calls.length = 0;

  const second = startReplyBridge({ ...base, since: "1700000000" });
  ok(await until(() => urls().length >= 1), "die neue Schleife verbindet sich wieder");
  ok(urls()[0].includes("since=msg_keep9"), "der fortgeschriebene Zeiger überlebt den Neustart");
  ok(!urls()[0].includes("1700000000"), "nicht der Startwert (keine Doppelverarbeitung)");
  second.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 27. Antwort-Brücke: Fehler beenden die Schleife nicht ────────────────
console.log("\nAntwort-Brücke (Robustheit)");
{
  resetSharedState();
  const runtime = replyRuntime();
  const requests = [];
  let failFirst = true;
  const fetchStub = async (input) => {
    requests.push(String(input));
    if (failFirst) {
      failFirst = false;
      throw new Error("Netzwerk weg");
    }
    return { ok: true, status: 200, text: async () => "" };
  };
  const started = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    reconnectMs: 20,
    log: () => {},
  });
  ok(await until(() => requests.length >= 2), "nach einem fetch-Fehler verbindet die Schleife erneut");
  eq(started.controller.signal.aborted, false, "der Fehler beendet die Schleife nicht");
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 27b. Antwort-Brücke: Stromende → Wiederverbindung ────────────────────
//
// ntfy beendet Streams (Serverneustart, Netzwechsel, keepalive-Abbruch). Die
// Brücke darf dann nicht still sterben, sondern muss sich neu verbinden — und
// dabei am letzten `since` ansetzen, damit nichts doppelt oder gar nicht ankommt.
console.log("\nAntwort-Brücke (Stromende)");
{
  resetSharedState();
  const runtime = replyRuntime();
  // Ziel der Antwort ist die zuletzt benachrichtigte Session (hier gesetzt,
  // damit die Meldung als Prompt ankommt statt als Hinweis „ohne Ziel-Session“).
  runtime.lastSessionID = SESSION;
  const prompts = [];
  const times = [];
  let round = 0;
  // Jeder Strom liefert genau eine Zeile und endet sofort: die Anzahl der
  // Verbindungen zeigt damit direkt die Wiederverbindungen.
  const fetchStub = (input, init = {}) => {
    round += 1;
    times.push(Date.now());
    const line = `${JSON.stringify({
      id: `msg_r${round}`,
      event: "message",
      title: "Nutzerantwort",
      message: `Runde ${round}`,
    })}\n`;
    return streamFetch([], [line])(input, init);
  };
  const started = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    controller: new AbortController(),
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    fetch: fetchStub,
    reconnectMs: 20,
    // Kein API-Passwort: der Formular-Weg bleibt inaktiv, dieser Test prüft nur
    // die Wiederverbindung (der fetch-Ersatz würde ihn sonst mitzählen).
    apiPassword: "",
    since: "1700000000",
    log: () => {},
  });

  ok(await until(() => times.length >= 1), "die erste Verbindung liefert eine Nachricht");
  ok(await until(() => prompts.length >= 1), "die Nachricht aus dem Strom kommt an");
  eq(prompts[0]?.text, "Runde 1", "die Nachricht aus dem Strom kommt als Prompt an");
  ok(await until(() => times.length >= 2), "nach dem Stromende verbindet die Brücke sich erneut");
  ok(await until(() => prompts.length >= 2), "auch die zweite Runde kommt an");
  eq(prompts[1]?.text, "Runde 2", "die Nachricht der zweiten Verbindung ist die neue");
  eq(started.controller.signal.aborted, false, "das Stromende beendet die Schleife nicht");
  ok(await until(() => times.filter(Boolean).length >= 3), "und die Schleife verbindet sich weiter");
  eq(new Set(prompts.map((prompt) => prompt.text)).size, prompts.length, "keine Nachricht kommt doppelt an");
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 27c. Antwort-Brücke: HTTP 429 → wachsender Abstand ───────────────────
//
// ntfy drosselt Aufrufer (Standard: 60 Anfragen Burst, Auffüllung alle 5 s).
// Eine 429-Antwort darf deshalb NICHT in einer schnellen Schleife wiederholt
// werden: die Wartezeit wächst deutlich und verdoppelt sich je Drosselung.
console.log("\nAntwort-Brücke (Drosselung)");
{
  resetSharedState();
  const runtime = replyRuntime();
  const logPath = testPath("ntfy-reply-bridge-429.log");
  rmSync(logPath, { force: true });
  const readLog = () => {
    try {
      return readFileSync(logPath, "utf8");
    } catch {
      return "";
    }
  };
  const times = [];
  const fetchStub = (input, init = {}) => {
    times.push(Date.now());
    return streamFetch([], [], 429)(input, init);
  };
  const started = startReplyBridge({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_REPLY_LOG: logPath,
    },
    runtime,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    reconnectMs: 20, // kurze Wartezeit: bei 429 darf sie NICHT greifen
    rateLimitMs: 100, // erste Wartezeit nach einer Drosselung
    rateLimitMaxMs: 400,
    since: "1700000000",
    log: () => {},
  });

  ok(await until(() => times.length >= 1), "der erste Versuch läuft in die Drosselung");
  await wait(60);
  eq(times.length, 1, "nach 429 wird nicht in der kurzen Wartezeit erneut gefragt");
  eq(started.controller.signal.aborted, false, "die Drosselung beendet die Schleife nicht");
  ok(await until(() => readLog().includes("429")), "die Drosselung wird diagnostiziert");

  ok(await until(() => times.length >= 2, 2000), "nach der langen Wartezeit wird erneut verbunden");
  ok(times[1] - times[0] >= 80, "der zweite Versuch kommt erst nach der langen Wartezeit");
  await wait(60);
  eq(times.length, 2, "auch der zweite Fehlschlag wiederholt sich nicht schnell");

  ok(await until(() => times.length >= 3, 2000), "die Schleife versucht es weiter");
  ok(
    times[2] - times[1] > times[1] - times[0],
    "die Wartezeit wächst bei jeder weiteren Drosselung (verdoppelnd)"
  );
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
  rmSync(logPath, { force: true });
}

// ── 28. Antwort-Brücke: Fallback-Session ohne vorherige Meldung ──────────
console.log("\nAntwort-Brücke (Fallback)");
{
  const runtime = { sentIds: [], processedIds: [], lastSessionID: "", lastActiveSession: "" };
  const prompts = [];
  const notifier = makeNotifier({ replyState: runtime });

  // Eine Session, die keine Meldung auslöst, ist trotzdem die „letzte
  // gesehene“ — und damit das Fallback-Ziel der Brücke.
  await notifier.handleEvent(
    v2("message.part.updated", { part: { type: "text", role: "user", text: "Hallo" } })
  );
  eq(runtime.lastActiveSession, SESSION, "handleEvent merkt die zuletzt gesehene Session");
  eq(runtime.lastSessionID, "", "eine Benachrichtigung gab es nicht");

  // Als Kind bekannte (Subagent-)Sessions zählen nicht als Fallback-Ziel.
  const childID = "ses_child0002abcdef";
  notifier._state.children.set(childID, SESSION);
  await notifier.handleEvent(
    v2("message.part.updated", { part: { type: "text", role: "user", text: "Kind" } }, { sessionID: childID })
  );
  eq(runtime.lastActiveSession, SESSION, "Kind-Sessions ändern das Fallback-Ziel nicht");

  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    // Kein API-Passwort: der Formular-Weg bleibt hier inaktiv (kein Netz im Test).
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    log: () => {},
  });
  const injected = await bridge.handleMessage({
    id: "msg_fb1",
    title: "Nutzerantwort",
    message: "Bitte weitermachen.",
  });
  eq(injected.action, "injected", "Antwort kommt auch ohne vorherige Meldung an");
  eq(prompts[0]?.sessionID, SESSION, "Ziel ist die zuletzt gesehene Session");

  // Die benachrichtigte Session hat immer Vorrang vor dem Fallback.
  runtime.lastSessionID = "ses_benachrichtigt3";
  await bridge.handleMessage({ id: "msg_fb2", title: "Nutzerantwort", message: "Und noch etwas." });
  eq(prompts[1]?.sessionID, "ses_benachrichtigt3", "lastSessionID hat Vorrang vor dem Fallback");
}

// ── 29. Antwort-Brücke: Diagnose-Log (NTFY_REPLY_LOG) ────────────────────
console.log("\nAntwort-Brücke (Diagnose-Log)");
{
  resetSharedState();
  const logPath = testPath("ntfy-reply-bridge-test.log");
  rmSync(logPath, { force: true });
  const readLog = () => {
    try {
      return readFileSync(logPath, "utf8");
    } catch {
      return "";
    }
  };
  const runtime = replyRuntime();
  runtime.lastSessionID = SESSION;
  const prompts = [];
  const fetchStub = async () => ({
    ok: true,
    status: 200,
    text: async () =>
      `${JSON.stringify({
        id: "msg_log1",
        event: "message",
        title: "Nutzerantwort",
        message: "Bitte Log prüfen.",
      })}\n`,
  });
  const started = startReplyBridge({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_REPLY_LOG: logPath,
    },
    runtime,
    // Kein API-Passwort: dieser Test prüft den Strom-Log + Prompt (kein Formular).
    apiPassword: "",
    controller: new AbortController(),
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    fetch: fetchStub,
    reconnectMs: 20,
    log: () => {},
  });
  ok(await until(() => readLog().includes("injected")), "die Aktion wird protokolliert");
  ok(readLog().includes("start") && readLog().includes("since="), "der Start steht mit since im Log");
  ok(readLog().includes("runde"), "jede Poll-Runde wird protokolliert");
  ok(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z /m.test(readLog()), "Zeilen tragen ISO-Zeitstempel");
  eq(prompts.length, 1, "die Nachricht aus dem Test wird genau einmal eingespeist");
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
  rmSync(logPath, { force: true });
}

// ── 30. Antwort-Brücke: setup → Abfrage → Prompt → Ack ───────────────────
console.log("\nAntwort-Brücke (Ende zu Ende)");
{
  resetSharedState();
  received.length = 0;

  const prompts = [];
  const posts = [];
  let releasePoll = null;
  let pollBody = "";
  const fetchStub = async (input, init = {}) => {
    const method = String(init.method ?? "GET").toUpperCase();
    // Formular-Abruf der Antwort-Brücke: in diesem Test ist keine Frage offen.
    if (method === "GET" && String(input).includes("/api/session/")) {
      return { ok: true, status: 200, json: async () => ({ data: [] }) };
    }
    if (method === "POST") {
      posts.push(JSON.parse(String(init.body ?? "{}")));
      return { ok: true, status: 200, json: async () => ({ id: `msg_post${posts.length}` }) };
    }
    await new Promise((resolve) => {
      releasePoll = resolve;
    });
    return { ok: true, status: 200, text: async () => pollBody };
  };

  const stream = makeStream();
  const ctx = {
    location: { directory: REPO_DIR },
    session: {
      hook: async () => ({ dispose: async () => {} }),
      prompt: async (arg) => {
        prompts.push(arg);
        return {};
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" },
    argv: ["serve", "--service"],
    fetch: fetchStub,
    log: () => {},
  });
  const cleanup = await runtime.setup(ctx);
  ok(await until(() => typeof releasePoll === "function"), "die Brücke fragt im Hintergrund ab");

  // Eine echte Benachrichtigung setzt die Zielsession (letzte Meldung).
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  stream.push({
    type: "message.part.updated",
    properties: { sessionID: SESSION, part: { type: "text", role: "assistant", text: "Fertig." } },
  });
  stream.push({ type: "session.idle", properties: { sessionID: SESSION } });
  ok(
    await until(() => posts.some((p) => String(p.title).includes("✅"))),
    "Fertig-Meldung ist raus"
  );

  // Jetzt antwortet der Nutzer im Topic.
  pollBody = JSON.stringify({
    id: "msg_user9",
    event: "message",
    title: "Nutzerantwort",
    message: "Bitte noch die Logs prüfen.",
  });
  releasePoll?.();
  ok(await until(() => prompts.length === 1), "Antwort wird als Prompt eingespeist");
  eq(prompts[0]?.sessionID, SESSION, "Prompt landet in der zuletzt benachrichtigten Session");
  eq(prompts[0]?.text, "Bitte noch die Logs prüfen.", "Nachrichtentext wird übergeben");
  ok(
    await until(() => posts.some((p) => String(p.title).startsWith("↩️"))),
    "Ack wird veröffentlicht"
  );
  ok(replyRuntime().sentIds.includes("msg_post1"), "eigene Message-ID wird gemerkt");

  cleanup?.();
  await wait();
  ok(stream.closed, "Cleanup beendet den Event-Stream");
  pollBody = "";
  releasePoll?.();
  resetSharedState();
}

// ── 31. Antwort-Brücke: Idempotenz (sekundengenaues `since`) ──────────────
// ntfy löst `since=<id>` nur sekundengenau auf: Nachrichten aus derselben
// Sekunde liefert ein Poll erneut aus. Dieselbe ID darf deshalb nur einmal
// verarbeitet werden.
console.log("\nAntwort-Brücke (Idempotenz)");
{
  const prompts = [];
  const published = [];
  const runtime = { sentIds: [], processedIds: [], lastSessionID: SESSION };
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    runtime,
    // Kein API-Passwort: hier geht es nur um Idempotenz (kein Netz im Test).
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async (payload) => {
      published.push(payload);
      return true;
    },
    log: () => {},
  });

  const first = await bridge.handleMessage({
    id: "msg_dup1",
    title: "Nutzerantwort",
    message: "Bitte Variante B umsetzen",
  });
  eq(first.action, "injected", "erster Aufruf wird eingespeist");
  eq(prompts.length, 1, "genau ein Prompt beim ersten Aufruf");
  eq(published.length, 1, "genau eine Ack-Veröffentlichung beim ersten Aufruf");

  const second = await bridge.handleMessage({
    id: "msg_dup1",
    title: "Nutzerantwort",
    message: "Bitte Variante B umsetzen",
  });
  eq(second.action, "duplicate", "zweiter Aufruf mit gleicher ID → duplicate");
  eq(prompts.length, 1, "kein weiterer Prompt bei der Wiederholung");
  eq(published.length, 1, "keine weitere Veröffentlichung bei der Wiederholung");

  // Ohne `id` gibt es keine Dedup-Basis — die Nachricht wird wie bisher verarbeitet.
  const noId1 = await bridge.handleMessage({ title: "Nutzerantwort", message: "Ohne ID" });
  const noId2 = await bridge.handleMessage({ title: "Nutzerantwort", message: "Ohne ID" });
  eq(noId1.action, "injected", "Nachricht ohne id wird verarbeitet");
  eq(noId2.action, "injected", "Nachricht ohne id gilt nicht als Duplikat");
  eq(prompts.length, 3, "beide Aufrufe ohne id erzeugen einen Prompt");

  // Der live beobachtete Fall: dieselbe Nachricht wird erneut ausgeliefert,
  // während keine Zielsession existiert — der Hinweis darf nur einmal kommen.
  published.length = 0;
  runtime.lastSessionID = "";
  const orphan1 = await bridge.handleMessage({ id: "msg_dup2", message: "Hallo?" });
  const orphan2 = await bridge.handleMessage({ id: "msg_dup2", message: "Hallo?" });
  eq(orphan1.action, "no-session", "erster Aufruf ohne Zielsession → Hinweis");
  eq(orphan2.action, "duplicate", "Wiederholung wird verworfen");
  eq(published.length, 1, "„ohne Ziel-Session“ erscheint genau einmal");
  ok(
    runtime.processedIds.includes("msg_dup1") && runtime.processedIds.includes("msg_dup2"),
    "verarbeitete IDs werden gemerkt"
  );
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
  const env = { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" };
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

// ── 33. Selbstheilender Ereignisstrom (Wiederaufbau nach Ende/Fehler) ─────
//
// Endet der Ereignisstrom (z. B. nach einem Plugin-Neuladen) oder wirft er,
// baut setup() ihn neu auf, statt still zu enden. Der Test zählt die
// subscribe()-Aufrufe; options.eventRetryMs verkürzt die Wartezeit.
console.log("\nEreignisstrom (Wiederaufbau)");
{
  resetSharedState();
  received.length = 0;
  let subscribes = 0;
  const ctx = {
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: {
      subscribe: () => {
        subscribes += 1;
        return {
          [Symbol.asyncIterator]() {
            return this;
          },
          next() {
            return Promise.reject(new Error("Strom weg"));
          },
          return() {
            return Promise.resolve({ value: undefined, done: true });
          },
        };
      },
    },
  };
  const runtime = createPluginRuntime({
    // NTFY_REPLY_ENABLED=0: hier geht es nur um den Ereignisstrom.
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_REPLY_ENABLED: "0",
      NTFY_PAIR_BUTTON: "0",
    },
    argv: ["serve", "--service"],
    log: () => {},
    eventRetryMs: 20,
  });
  const cleanup = await runtime.setup(ctx);
  eq(typeof cleanup, "function", "setup liefert eine Cleanup-Funktion");
  ok(await until(() => subscribes > 1, 2000), "nach einem Fehler wird neu abonniert");

  cleanup?.();
  await wait(100);
  const settled = subscribes;
  await wait(150);
  eq(subscribes, settled, "nach teardown() endet die Schleife (kein weiteres Abonnement)");
}

// ── 34. Diagnose des Ereignisstroms (NTFY_REPLY_LOG) ─────────────────────
//
// Aufbau (event-strom-start), der erste Event je Typ (ereignis) und das Ende
// (event-strom-ende) landen in der Diagnose-Datei — im Betrieb über
// /etc/opencode.env als NTFY_REPLY_LOG gesetzt. Jeder Typ wird nur einmal
// protokolliert, damit Deltas das Log nicht fluten.
console.log("\nEreignisstrom (Diagnose)");
{
  resetSharedState();
  received.length = 0;
  const logPath = testPath("ntfy-event-strom-test.log");
  rmSync(logPath, { force: true });
  const readLog = () => {
    try {
      return readFileSync(logPath, "utf8");
    } catch {
      return "";
    }
  };
  const stream = makeStream();
  const ctx = {
    location: { directory: REPO_DIR },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: {
      subscribe: ({ signal } = {}) => {
        if (signal) signal.addEventListener("abort", () => stream.close());
        return stream.iterator;
      },
    },
  };
  const runtime = createPluginRuntime({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_REPLY_ENABLED: "0",
      NTFY_PAIR_BUTTON: "0",
      NTFY_REPLY_LOG: logPath,
    },
    argv: ["serve", "--service"],
    log: () => {},
  });
  const cleanup = await runtime.setup(ctx);
  ok(
    await until(() => readLog().includes("event-strom-start")),
    "der Strom-Aufbau wird protokolliert"
  );
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  ok(
    await until(() => readLog().includes("ereignis: session.created")),
    "der erste Ereignistyp wird protokolliert"
  );
  // Derselbe Typ noch einmal: die Diagnose bleibt gedrosselt.
  stream.push({ type: "session.created", properties: { info: info(SESSION) } });
  await wait(80);
  eq(
    readLog().split("\n").filter((line) => line.includes("ereignis: session.created")).length,
    1,
    "je Ereignistyp genau eine Diagnosezeile"
  );
  cleanup?.();
  ok(
    await until(() => readLog().includes("event-strom-ende"), 1000),
    "das Strom-Ende wird protokolliert"
  );
  rmSync(logPath, { force: true });
}

// ── 35. Formular-Antwort: offene native Frage direkt beantworten ──────────
//
// Ist eine native Frage offen (question-Tool = serverseitiges Formular), trägt
// die Brücke die Antwort per POST …/reply ein, sofern der Handy-Text eindeutig
// zuordenbar ist (exakter Options-Treffer oder Optionsindex) oder das Feld
// Freitext/Strings akzeptiert. Ohne eindeutigen Treffer wird die Frage
// abgebrochen (DELETE …/form/{id}) und der Text als Prompt eingespeist — eine
// Frage bleibt so nie offen stehen (kein blockierter Turn). Alles hier läuft
// mit gemocktem fetch: kein Netz.
console.log("\nFormular-Antwort (offene Frage)");

/** Auswahlfeld laut Schema: StringField mit options ({ value, label }). */
const choiceField = {
  key: "q0",
  type: "string",
  title: "Wie weiter?",
  options: [
    { value: "a", label: "Deployen" },
    { value: "b", label: "Warten" },
  ],
};

/** Formular wie in der echten Nutzlast (metadata.kind = "question"). */
function openForm(id, fields, metadata = { kind: "question" }) {
  return { id, sessionID: SESSION, title: "Questions", metadata, fields };
}

/**
 * Brücke mit gemocktem fetch: GET …/form liefert `forms`, POST …/reply
 * antwortet mit `replyStatus`. Das API-Passwort kommt aus der Umgebung — also
 * genau der Weg, den die Brücke auch im Betrieb nimmt.
 */
function makeFormBridge(extra = {}) {
  const requests = [];
  const prompts = [];
  const published = [];
  const fetchStub = async (input, init = {}) => {
    const url = String(input);
    const method = String(init.method ?? "GET").toUpperCase();
    requests.push({ url, method, init });
    if (extra.throwOnForms) throw new Error("Netzwerk weg");
    if (method === "POST") {
      const status = extra.replyStatus ?? 204;
      return { ok: status < 400, status, json: async () => ({}) };
    }
    if (method === "DELETE") {
      const status = extra.deleteStatus ?? 204;
      // Abbrechen: bei Erfolg verschwindet das Formular aus der offenen Liste
      // (in place, damit der Test dieselbe Liste weiterlesen kann) — so ist
      // sichtbar, dass wirklich keine Frage mehr offen bleibt.
      if (status < 400) {
        const formID = decodeURIComponent(String(url).split("/form/")[1]?.split("/")[0] ?? "");
        if (Array.isArray(extra.forms)) {
          for (let i = extra.forms.length - 1; i >= 0; i -= 1) {
            if (extra.forms[i]?.id === formID) extra.forms.splice(i, 1);
          }
        }
      }
      return { ok: status < 400, status, json: async () => ({}) };
    }
    const status = extra.formsStatus ?? 200;
    return { ok: status < 400, status, json: async () => ({ data: extra.forms ?? [] }) };
  };
  const runtime = { sentIds: [], processedIds: [], lastSessionID: SESSION };
  const bridge = createReplyBridge({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_SESSION_PORT: "49374",
      OPENCODE_SERVER_PASSWORD: "pw_test",
      ...(extra.env ?? {}),
    },
    runtime,
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async (payload) => {
      published.push(payload);
      return true;
    },
    fetch: fetchStub,
    log: () => {},
  });
  const posts = () => requests.filter((request) => request.method === "POST");
  const gets = () => requests.filter((request) => request.method === "GET");
  const deletes = () => requests.filter((request) => request.method === "DELETE");
  const bodyOf = (request) => JSON.parse(String(request?.init?.body ?? "{}"));
  return { bridge, runtime, requests, prompts, published, posts, gets, deletes, bodyOf };
}

// a) Nummer als Optionsindex → Antwort landet im Formular, kein Prompt.
{
  const { bridge, prompts, published, posts, gets, deletes, bodyOf } = makeFormBridge({
    forms: [openForm("frm_1", [choiceField])],
  });
  const result = await bridge.handleMessage({ id: "msg_f1", title: "Nutzerantwort", message: "2" });
  eq(result.action, "replied", "Antwort '2' landet in der offenen Frage");
  eq(prompts.length, 0, "kein Prompt — der blockierte Turn läuft über das Formular weiter");
  eq(gets().length, 1, "offene Formulare werden genau einmal abgefragt");
  eq(
    gets()[0]?.url,
    `http://127.0.0.1:49374/api/session/${SESSION}/form`,
    "Abruf geht an die lokale API (Port aus NTFY_SESSION_PORT)"
  );
  eq(
    gets()[0]?.init?.headers?.Authorization,
    `Basic ${Buffer.from("opencode:pw_test").toString("base64")}`,
    "HTTP-Basic mit dem API-Passwort aus der Umgebung"
  );
  eq(posts().length, 1, "genau ein Reply-POST");
  eq(
    posts()[0]?.url,
    `http://127.0.0.1:49374/api/session/${SESSION}/form/frm_1/reply`,
    "Reply geht an den Formular-Endpunkt"
  );
  eq(
    String(posts()[0]?.init?.headers?.["Content-Type"] ?? "").includes("application/json"),
    true,
    "Reply trägt einen JSON-Body"
  );
  eq(bodyOf(posts()[0])?.answer?.q0, "b", "gesendet wird der Wert der 2. Option");
  eq(published.length, 1, "genau eine Bestätigung");
  eq(
    published[0]?.title,
    "↩️ Antwort in die offene Frage eingetragen",
    "Bestätigung nennt die eingetragene Antwort"
  );
  eq(published[0]?.priority, 2, "Bestätigung ist leise (Priorität 2)");
  eq(deletes().length, 0, "kein Abbruch bei eindeutigem Treffer");
}

// b) Treffer über das Label (case-insensitive) und über den Wert.
{
  const { bridge, prompts, posts, deletes, bodyOf } = makeFormBridge({
    forms: [openForm("frm_2", [choiceField])],
  });
  const byLabel = await bridge.handleMessage({ id: "msg_f2", title: "Nutzerantwort", message: "warten" });
  eq(byLabel.action, "replied", "Label-Treffer ohne Rücksicht auf Groß-/Kleinschreibung");
  eq(bodyOf(posts()[0])?.answer?.q0, "b", "Label 'warten' → Optionswert 'b'");
  const byValue = await bridge.handleMessage({ id: "msg_f2b", title: "Nutzerantwort", message: "a" });
  eq(byValue.action, "replied", "Treffer über den Optionswert");
  eq(bodyOf(posts()[1])?.answer?.q0, "a", "Wert 'a' wird direkt gesendet");
  const byQuoted = await bridge.handleMessage({
    id: "msg_f2c",
    title: "Nutzerantwort",
    message: '  "warten"  ',
  });
  eq(byQuoted.action, "replied", "umschließende Anführungszeichen und Leerraum werden entfernt");
  eq(bodyOf(posts()[2])?.answer?.q0, "b", "auch die zitierte Antwort trifft die Option");
  eq(prompts.length, 0, "kein Prompt bei eindeutigem Treffer");
  eq(deletes().length, 0, "kein Abbruch bei eindeutigem Treffer");
}

// c) Ganzer Satz ohne Treffer: der Rohtext wird versucht; lehnt die API ihn
//    ab (400), wird die offene Frage abgebrochen und der Text als Prompt
//    übergeben — sie bleibt nicht offen stehen (kein blockierter Turn).
{
  const forms = [openForm("frm_3", [choiceField])];
  const { bridge, prompts, published, posts, deletes, bodyOf } = makeFormBridge({
    forms,
    replyStatus: 400,
  });
  const result = await bridge.handleMessage({
    id: "msg_f3",
    title: "Nutzerantwort",
    message: "vielleicht später",
  });
  eq(result.action, "cancelled", "ohne Treffer wird die Frage abgebrochen");
  eq(posts().length, 1, "zuerst wird der Rohtext als Antwort versucht");
  eq(bodyOf(posts()[0])?.answer?.q0, "vielleicht später", "der Rohtext geht als Antwort raus");
  eq(deletes().length, 1, "die offene Frage wird abgebrochen (DELETE)");
  eq(
    deletes()[0]?.url,
    `http://127.0.0.1:49374/api/session/${SESSION}/form/frm_3`,
    "Abbruch geht an den Formular-Endpunkt (ohne /reply)"
  );
  eq(deletes()[0]?.init?.method, "DELETE", "Abbruch nutzt die DELETE-Methode");
  eq(forms.length, 0, "kein offenes Formular mehr");
  eq(prompts.length, 1, "der Text kommt zusätzlich als Prompt an");
  eq(prompts[0]?.text, "vielleicht später", "der Prompt trägt den ganzen Satz");
  eq(
    published[0]?.title,
    "↩️ Frage aufgelöst, Antwort als Nachricht übergeben",
    "Bestätigung nennt das Auflösen der Frage"
  );
  eq(published[0]?.priority, 2, "Bestätigung ist leise (Priorität 2)");
}
{
  // Akzeptiert die API den Rohtext (204), zählt er wie ein eindeutiger
  // Treffer: die Frage ist direkt beantwortet, es wird nichts abgebrochen.
  const { bridge, prompts, posts, deletes, bodyOf } = makeFormBridge({
    forms: [openForm("frm_3a", [choiceField])],
  });
  const result = await bridge.handleMessage({
    id: "msg_f3a",
    title: "Nutzerantwort",
    message: "vielleicht später",
  });
  eq(result.action, "replied", "akzeptierter Rohtext wird wie ein Treffer bestätigt");
  eq(posts().length, 1, "genau ein Reply-POST");
  eq(bodyOf(posts()[0])?.answer?.q0, "vielleicht später", "gesendet wird der Rohtext");
  eq(deletes().length, 0, "kein Abbruch bei akzeptiertem Rohtext");
  eq(prompts.length, 0, "kein Prompt — die Frage wurde direkt beantwortet");
}
{
  // Zahl außerhalb des Optionsbereichs ist kein Index: es wird kein
  // Optionswert geraten, sondern der Rohtext versucht (hier abgelehnt).
  const { bridge, prompts, posts, deletes, bodyOf } = makeFormBridge({
    forms: [openForm("frm_3b", [choiceField])],
    replyStatus: 400,
  });
  const result = await bridge.handleMessage({ id: "msg_f3b", message: "7" });
  eq(result.action, "cancelled", "Optionsindex außerhalb der Liste wird nicht geraten");
  eq(posts().length, 1, "der Rohtext wird versucht");
  eq(bodyOf(posts()[0])?.answer?.q0, "7", "gesendet wird die Zahl als Rohtext, kein Optionswert");
  eq(deletes().length, 1, "die Frage wird abgebrochen");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}
{
  // Reine Teilstring-Ähnlichkeit ist KEIN Treffer: „Kopplung“ passt nur
  // zufällig in „Kopplung hakt noch“ — nicht raten, sondern abbrechen.
  const { bridge, prompts, posts, deletes, bodyOf } = makeFormBridge({
    forms: [
      openForm("frm_3c", [
        {
          key: "q0",
          type: "string",
          title: "Wie weiter?",
          options: [{ value: "hakt", label: "Kopplung hakt noch" }],
        },
      ]),
    ],
    replyStatus: 400,
  });
  const result = await bridge.handleMessage({ id: "msg_f3c", message: "Kopplung" });
  eq(result.action, "cancelled", "Teilstring-Ähnlichkeit führt nicht zu einem Formular-Reply");
  eq(posts().length, 1, "der Rohtext wird versucht");
  eq(bodyOf(posts()[0])?.answer?.q0, "Kopplung", "gesendet wird der Rohtext, nicht der Optionswert");
  eq(deletes().length, 1, "die Frage wird abgebrochen");
  eq(prompts.length, 1, "der ganze Satz kommt als Prompt an");
}

// d) Freitext-Feld (String-Typ) → der Text wird als Wert gesendet.
{
  const { bridge, prompts, posts, bodyOf } = makeFormBridge({
    forms: [
      openForm("frm_4", [{ key: "q0", type: "string", title: "Wie lautet der Zweck?" }]),
    ],
  });
  const result = await bridge.handleMessage({ id: "msg_f4", message: "  produktiv  " });
  eq(result.action, "replied", "Freitext-Feld wird mit dem Text beantwortet");
  eq(bodyOf(posts()[0])?.answer?.q0, "produktiv", "getrimmter Text wird als Wert gesendet");
  eq(prompts.length, 0, "kein Prompt beim Freitext-Feld");
}
{
  // Unbekannter Feldtyp (number) bleibt konservativ: kein Reply-Versuch,
  // aber die Frage wird abgebrochen — sie darf nicht offen bleiben.
  const forms = [openForm("frm_4b", [{ key: "q0", type: "number", title: "Wie viele?" }])];
  const { bridge, posts, prompts, deletes, published } = makeFormBridge({ forms });
  const result = await bridge.handleMessage({ id: "msg_f4b", message: "42" });
  eq(result.action, "cancelled", "unbekannter Feldtyp → die Frage wird abgebrochen");
  eq(posts().length, 0, "kein Reply-POST für unbekannte Feldtypen");
  eq(deletes().length, 1, "der Abbruch geht raus");
  eq(forms.length, 0, "kein offenes Formular mehr");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
  eq(
    published[0]?.title,
    "↩️ Frage aufgelöst, Antwort als Nachricht übergeben",
    "Bestätigung nennt das Auflösen der Frage"
  );
}

// e) Mehrere Felder bzw. mehrere Formulare → nicht raten, aber die offenen
//    Fragen werden abgebrochen (sie dürfen den Turn nicht blockieren).
{
  const forms = [openForm("frm_5", [choiceField, { key: "q1", type: "string", title: "Warum?" }])];
  const { bridge, prompts, posts, deletes, published } = makeFormBridge({ forms });
  const result = await bridge.handleMessage({ id: "msg_f5", message: "Warten" });
  eq(result.action, "cancelled", "zwei Felder → konservativ, die Frage wird abgebrochen");
  eq(posts().length, 0, "kein Reply-POST bei zwei Feldern");
  eq(deletes().length, 1, "die Frage wird abgebrochen");
  eq(forms.length, 0, "kein offenes Formular mehr");
  eq(prompts.length, 1, "stattdessen Prompt-Einspeisung");
  eq(
    published[0]?.title,
    "↩️ Frage aufgelöst, Antwort als Nachricht übergeben",
    "Bestätigung nennt das Auflösen der Frage"
  );
}
{
  const forms = [openForm("frm_6", [choiceField]), openForm("frm_7", [choiceField])];
  const { bridge, prompts, posts, deletes } = makeFormBridge({ forms });
  const result = await bridge.handleMessage({ id: "msg_f6", message: "Warten" });
  eq(result.action, "cancelled", "zwei offene Fragen → konservativ, beide werden abgebrochen");
  eq(posts().length, 0, "kein Reply-POST bei zwei Formularen");
  eq(deletes().length, 2, "beide Fragen werden abgebrochen");
  eq(forms.length, 0, "kein offenes Formular mehr");
  eq(prompts.length, 1, "stattdessen Prompt-Einspeisung");
}

// f) Reply wird abgelehnt (400) → Fallback auf Prompt + Diagnose-Log.
//    (Die Antwort war eindeutig zuordenbar; bleibt der Reply trotzdem
//    erfolglos, wird wie bisher nicht abgebrochen, sondern eingespeist.)
{
  const logPath = testPath("ntfy-form-reply-test.log");
  rmSync(logPath, { force: true });
  const { bridge, prompts, published, posts, deletes } = makeFormBridge({
    forms: [openForm("frm_8", [choiceField])],
    replyStatus: 400,
    env: { NTFY_REPLY_LOG: logPath },
  });
  const result = await bridge.handleMessage({ id: "msg_f8", message: "Warten" });
  eq(result.action, "injected", "abgelehnter Reply (400) fällt auf den Prompt zurück");
  eq(posts().length, 1, "der Reply wurde versucht");
  eq(deletes().length, 0, "kein Abbruch: die Antwort war eindeutig zuordenbar");
  eq(prompts.length, 1, "der Text wird als Prompt eingespeist");
  eq(published[0]?.title, "↩️ Antwort an Agenten weitergegeben", "normale Bestätigung");
  ok(
    readFileSync(logPath, "utf8").includes("form-reply-fehler"),
    "der API-Fehler steht im Diagnose-Log"
  );
  rmSync(logPath, { force: true });
}
{
  // 409 = die Frage ist schon beantwortet → ebenfalls Prompt-Fallback.
  const { bridge, prompts, posts } = makeFormBridge({
    forms: [openForm("frm_8b", [choiceField])],
    replyStatus: 409,
  });
  const result = await bridge.handleMessage({ id: "msg_f8b", message: "Warten" });
  eq(result.action, "injected", "409 (schon beantwortet) fällt auf den Prompt zurück");
  eq(posts().length, 1, "der Reply wurde versucht");
  eq(prompts.length, 1, "der Text wird als Prompt eingespeist");
}

// g) Formular-Abruf scheitert (Netzfehler bzw. 500) → wie bisher.
{
  const { bridge, prompts } = makeFormBridge({
    forms: [openForm("frm_9", [choiceField])],
    throwOnForms: true,
  });
  const result = await bridge.handleMessage({ id: "msg_f9", message: "Warten" });
  eq(result.action, "injected", "fetch-Fehler beim Abruf → Prompt-Einspeisung");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}
{
  const { bridge, prompts } = makeFormBridge({
    forms: [openForm("frm_10", [choiceField])],
    formsStatus: 500,
  });
  const result = await bridge.handleMessage({ id: "msg_f10", message: "Warten" });
  eq(result.action, "injected", "HTTP 500 beim Abruf → Prompt-Einspeisung");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}

// h) NTFY_FORM_REPLY=0 → die lokale API wird nie gefragt.
{
  const { bridge, requests, prompts } = makeFormBridge({
    forms: [openForm("frm_11", [choiceField])],
    env: { NTFY_FORM_REPLY: "0" },
  });
  const result = await bridge.handleMessage({ id: "msg_f11", message: "Warten" });
  eq(result.action, "injected", "NTFY_FORM_REPLY=0 → immer Prompt-Einspeisung");
  eq(requests.length, 0, "kein Formular-Abruf, wenn der Schalter aus ist");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}

// i) Ohne API-Passwort bleibt die lokale API unangetastet.
{
  const requests = [];
  const prompts = [];
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime: { sentIds: [], processedIds: [], lastSessionID: SESSION },
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    apiPassword: "",
    fetch: async (input, init = {}) => {
      requests.push({ url: String(input), method: init.method });
      throw new Error("ohne Passwort darf gar nicht gefragt werden");
    },
    log: () => {},
  });
  const result = await bridge.handleMessage({ id: "msg_f12", message: "Warten" });
  eq(result.action, "injected", "ohne Passwort bleibt es bei der Prompt-Einspeisung");
  eq(requests.length, 0, "ohne Passwort wird die lokale API gar nicht gefragt");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}

// j) multiselect bekommt laut Schema eine Liste; Freigabe-Dialoge bleiben
//    dem nativen Fenster vorbehalten.
{
  const { bridge, posts, bodyOf } = makeFormBridge({
    forms: [
      openForm("frm_13", [
        {
          key: "q0",
          type: "multiselect",
          title: "Welche Varianten?",
          options: [
            { value: "x", label: "Variante X" },
            { value: "y", label: "Variante Y" },
          ],
        },
      ]),
    ],
  });
  const result = await bridge.handleMessage({ id: "msg_f13", message: "1" });
  eq(result.action, "replied", "Auswahl per Index auch beim multiselect-Feld");
  eq(bodyOf(posts()[0])?.answer?.q0?.[0], "x", "multiselect bekommt eine Liste mit dem Wert");
}
{
  const { bridge, prompts, posts } = makeFormBridge({
    forms: [openForm("frm_14", [choiceField], { kind: "permission" })],
  });
  const result = await bridge.handleMessage({ id: "msg_f14", message: "Warten" });
  eq(result.action, "injected", "Freigabe-Formular wird nicht per Handy-Text beantwortet");
  eq(posts().length, 0, "kein Reply-POST für Freigaben");
  eq(prompts.length, 1, "der Text kommt als Prompt an");
}

// k) Idempotenz bleibt die erste Prüfung — auch als Formular-Antwort.
{
  const { bridge, posts } = makeFormBridge({ forms: [openForm("frm_15", [choiceField])] });
  const first = await bridge.handleMessage({ id: "msg_f15", message: "Warten" });
  const second = await bridge.handleMessage({ id: "msg_f15", message: "Warten" });
  eq(first.action, "replied", "erster Aufruf landet in der offenen Frage");
  eq(second.action, "duplicate", "derselbe Text wird kein zweites Mal beantwortet");
  eq(posts().length, 1, "genau ein Reply-POST trotz Wiederholung");
}

// m) Der Abbruch scheitert (500): der Text wird trotzdem eingespeist und der
//    Fehler landet im Diagnose-Log — die Brücke darf nie werfen.
{
  const logPath = testPath("ntfy-form-cancel-test.log");
  rmSync(logPath, { force: true });
  const forms = [openForm("frm_16", [choiceField])];
  const { bridge, prompts, published, posts, deletes } = makeFormBridge({
    forms,
    replyStatus: 400,
    deleteStatus: 500,
    env: { NTFY_REPLY_LOG: logPath },
  });
  const result = await bridge.handleMessage({
    id: "msg_f16",
    message: "Kopplung prüfen bitte",
  });
  eq(result.action, "injected", "fehlgeschlagener Abbruch → trotzdem Prompt-Einspeisung");
  eq(posts().length, 1, "der Rohtext wurde als Antwort versucht");
  eq(deletes().length, 1, "der Abbruch wurde versucht");
  eq(forms.length, 1, "die Frage bleibt in diesem Fall offen");
  eq(prompts.length, 1, "kein Datenverlust: der Text kommt als Prompt an");
  eq(published[0]?.title, "↩️ Antwort an Agenten weitergegeben", "normale Bestätigung");
  ok(
    readFileSync(logPath, "utf8").includes("form-abbruch-fehler"),
    "der fehlgeschlagene Abbruch steht im Diagnose-Log"
  );
  rmSync(logPath, { force: true });
}

// l) Hinweiszeile in der Frage-Meldung.
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(
    v2("form.created", {
      questions: [{ question: "Weiter mit Phase 4?", options: [{ label: "Ja" }, { label: "Nein" }] }],
    })
  );
  const message = String(received[0]?.body?.message ?? "");
  ok(message.includes("Antwort direkt hier möglich"), "Frage-Meldung trägt die Antwort-Hinweiszeile");
  ok(message.includes("Nummer der Option oder freier Text"), "Hinweis nennt Nummer und freien Text");
  eq(
    message.trim().split("\n").pop(),
    "Antwort direkt hier möglich: Nummer der Option oder freier Text.",
    "Hinweis steht als eigene Zeile am Ende"
  );
}
{
  const n = makeNotifier({ env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_FORM_REPLY: "0" } });
  received.length = 0;
  await n.handleEvent(
    v2("form.created", { questions: [{ question: "Weiter?", options: [{ label: "Ja" }] }] })
  );
  ok(
    !String(received[0]?.body?.message ?? "").includes("Antwort direkt hier möglich"),
    "bei NTFY_FORM_REPLY=0 entfällt der Hinweis"
  );
}

// ── 33. Kopplungslink als Button (gemocktes fetch, kein Netz) ─────────────
//
// Jede Meldung trägt einen frisch erzeugten Kopplungslink (POST /api/pair
// liefert einen einmaligen, 5 Minuten gültigen Code). Der Link wird ~120 s
// zwischengespeichert, ist über NTFY_PAIR_BUTTON=0 abschaltbar und darf die
// Meldung nie scheitern lassen. Alles hier läuft mit gemocktem fetch.
console.log("\nKopplungslink");

/** fetch-Ersatz: /api/pair liefert einen Code (oder wirft), alles andere ist Publish. */
function pairFetchStub(options = {}) {
  const requests = [];
  const published = [];
  const stub = async (input, init = {}) => {
    const target = String(input);
    requests.push({ url: target, init });
    if (target.includes("/api/pair")) {
      if (options.throwOnPair) throw new Error("Netzwerk weg");
      const status = options.pairStatus ?? 200;
      const code = `code_${requests.filter((request) => request.url.includes("/api/pair")).length}`;
      return {
        ok: status < 400,
        status,
        json: async () => options.pair ?? { code, expires_in: 300 },
      };
    }
    published.push(JSON.parse(String(init.body ?? "{}")));
    return { ok: true, status: 200, json: async () => ({ id: `msg_k${published.length}` }) };
  };
  const pairCalls = () => requests.filter((request) => request.url.includes("/api/pair"));
  const pairActions = (index = 0) =>
    (published[index]?.actions ?? []).filter((action) => action.label === "Koppeln (5 Min)");
  return { stub, requests, published, pairCalls, pairActions };
}

const PAIR_BASE = "https://49374--main--infrastructure--carstenmueller2002-coder.mueller-nas.de";

// a) Erfolgsfall: genau eine Koppeln-Aktion mit der Session-Basis; der
//    API-Aufruf trägt Basic-Auth (Nutzer opencode), das Passwort bleibt aus dem Log.
{
  resetSharedState();
  const { stub, published, pairCalls, pairActions } = pairFetchStub();
  const logged = [];
  const n = createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_PAIR_BUTTON: "1",
      OPENCODE_SERVER_PASSWORD: "pw_test",
    },
    state: freshState(),
    fetch: stub,
    log: (...args) => logged.push(args.join(" ")),
  });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.sendOnce(SESSION, "idle", { title: "✅ Fertig", message: "Text", click: PAIR_BASE });

  eq(published.length, 1, "genau eine Meldung geht raus");
  eq(pairCalls().length, 1, "genau ein /api/pair-Aufruf");
  eq(pairCalls()[0]?.url, "http://127.0.0.1:49374/api/pair", "Aufruf geht an die lokale API");
  eq(pairCalls()[0]?.init?.method, "POST", "der Code wird per POST erzeugt");
  eq(
    pairCalls()[0]?.init?.headers?.Authorization,
    `Basic ${Buffer.from("opencode:pw_test").toString("base64")}`,
    "HTTP-Basic mit Nutzer opencode"
  );
  eq(pairActions().length, 1, "genau eine Koppeln-Aktion trägt die Meldung");
  eq(pairActions()[0]?.action, "view", "Koppeln ist eine view-Aktion");
  eq(
    pairActions()[0]?.url,
    `${PAIR_BASE}/auth/connect/code_1`,
    "Kopplungs-URL nutzt die Basis der Session-Links"
  );
  eq(published[0]?.click, PAIR_BASE, "das Click-Ziel bleibt die Session-URL");
  eq(published[0]?.actions?.[0]?.label, "Session öffnen", "die Session-Aktion steht zuerst");
  ok(!logged.join("\n").includes("pw_test"), "das API-Passwort steht nicht im Log");
}

// b) Zwischenspeicher: zwei Meldungen kurz hintereinander → ein API-Aufruf und
//    derselbe Link; erst nach ~120 s entsteht ein neuer Code.
{
  resetSharedState();
  const { stub, published, pairCalls, pairActions } = pairFetchStub();
  let clock = 1000000;
  const n = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "1" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    now: () => clock,
    log: () => {},
  });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });

  await n.sendOnce(SESSION, "idle", { title: "eins", message: "Text 1", click: PAIR_BASE });
  clock += 50000; // 50 s später: der Zwischenspeicher gilt weiter
  await n.sendOnce(SESSION, "error", { title: "zwei", message: "Text 2", click: PAIR_BASE });

  eq(published.length, 2, "beide Meldungen gehen raus");
  eq(pairCalls().length, 1, "zwei Meldungen kurz hintereinander → ein /api/pair-Aufruf");
  ok(
    pairActions(0)[0]?.url === pairActions(1)[0]?.url,
    "beide Meldungen tragen denselben Kopplungslink"
  );

  clock += 100000; // zusammen 150 s: der Zwischenspeicher ist abgelaufen
  await n.sendOnce(SESSION, "question", { title: "drei", message: "Text 3", click: PAIR_BASE });
  eq(pairCalls().length, 2, "nach ~120 s entsteht ein neuer Code");
  ok(pairActions(2)[0]?.url !== pairActions(0)[0]?.url, "der neue Link unterscheidet sich");
}

// c) Fehlerfall: die Meldung geht ohne Button raus, nichts wird geworfen, der
//    Fehler landet im Diagnose-Log — erst bei HTTP 500, dann bei Netzfehler.
{
  resetSharedState();
  const logPath = testPath("ntfy-pair-test.log");
  rmSync(logPath, { force: true });
  const { stub, published, pairCalls, pairActions } = pairFetchStub({ pairStatus: 500 });
  const n = createNtfyNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_PAIR_BUTTON: "1",
      NTFY_REPLY_LOG: logPath,
    },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  const sent = await n.sendOnce(SESSION, "idle", { title: "t", message: "m", click: PAIR_BASE });

  eq(sent, true, "die Meldung geht trotz API-Fehler raus");
  eq(published.length, 1, "genau eine Meldung");
  eq(pairCalls().length, 1, "der Aufruf wurde versucht");
  eq(pairActions().length, 0, "kein Koppeln-Button bei HTTP 500");
  eq(published[0]?.actions?.[0]?.label, "Session öffnen", "die Session-Aktion bleibt erhalten");
  ok(
    readFileSync(logPath, "utf8").includes("pair-link-fehler"),
    "der Fehler steht als pair-link-fehler im Diagnose-Log"
  );
  rmSync(logPath, { force: true });
}
{
  resetSharedState();
  const { stub, published, pairActions } = pairFetchStub({ throwOnPair: true });
  const n = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "1" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  const sent = await n.sendOnce(SESSION, "idle", { title: "t", message: "m", click: PAIR_BASE });
  eq(sent, true, "ein Netzfehler beim Kopplungslink lässt die Meldung unberührt");
  eq(published.length, 1, "genau eine Meldung");
  eq(pairActions().length, 0, "kein Koppeln-Button bei Netzfehler");
}
{
  // Ohne API-Passwort wird die lokale API gar nicht erst gefragt.
  resetSharedState();
  const { stub, published, pairCalls, pairActions } = pairFetchStub();
  const n = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "1" },
    apiPassword: "",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  const sent = await n.sendOnce(SESSION, "idle", { title: "t", message: "m", click: PAIR_BASE });
  eq(sent, true, "ohne Passwort geht die Meldung trotzdem raus");
  eq(published.length, 1, "genau eine Meldung");
  eq(pairCalls().length, 0, "ohne Passwort kein /api/pair-Aufruf");
  eq(pairActions().length, 0, "ohne Passwort kein Koppeln-Button");
}

// d) NTFY_PAIR_BUTTON=0/"false": kein Button, kein API-Aufruf.
{
  resetSharedState();
  const { stub, published, pairCalls, pairActions } = pairFetchStub();
  const n = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  await n.sendOnce(SESSION, "idle", { title: "t", message: "m", click: PAIR_BASE });
  eq(published.length, 1, "die Meldung geht trotzdem raus");
  eq(pairCalls().length, 0, "NTFY_PAIR_BUTTON=0 → kein API-Aufruf");
  eq(pairActions().length, 0, "NTFY_PAIR_BUTTON=0 → kein Koppeln-Button");
  eq(published[0]?.click, PAIR_BASE, "das Click-Ziel bleibt unverändert");

  const off = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "false" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  await off.sendOnce(SESSION, "idle", { title: "t", message: "m", click: PAIR_BASE });
  eq(pairCalls().length, 0, "auch \"false\" schaltet den Button ab");
}

// e) Kein Doppel-Button und kein doppeltes Click-Ziel: trägt die Meldung die
//    Aktionen schon, wird nichts ergänzt — das Click-Ziel bleibt unverändert.
{
  resetSharedState();
  const { stub, published, pairActions } = pairFetchStub();
  const n = createNtfyNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "1" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  await n.sendOnce(SESSION, "idle", {
    title: "mit-aktionen",
    message: "Text",
    click: PAIR_BASE,
    actions: [
      { action: "view", label: "Session öffnen", url: PAIR_BASE },
      { action: "view", label: "Koppeln (5 Min)", url: `${PAIR_BASE}/auth/connect/code_1` },
    ],
  });
  eq(published.length, 1, "die Meldung geht raus");
  eq(published[0]?.actions?.length, 2, "vorhandene Aktionen werden nicht verdoppelt");
  eq(pairActions().length, 1, "genau eine Koppeln-Aktion (keine zweite)");
  eq(published[0]?.click, PAIR_BASE, "das bestehende Click-Ziel bleibt unverändert");
}

// ── 34. Klickziel: Session-Deep-Link der OpenCode-Oberfläche ─────────────
//
// Die Oberfläche hat die Route /server/<serverKey>/session/<id> (Solid-Router
// mit Path-Routing); <serverKey> ist base64url des Ursprungs der Basis (im
// Browser gemessen — "local" ließ die UI abstürzen). Jeder Klick zeigt deshalb
// auf den Deep-Link der Session statt nur auf die Basis: der Schlüssel kommt
// aus NTFY_SERVER_KEY, wenn gesetzt, sonst wird er aus der Basis berechnet;
// die Session-ID wird URL-sicher eingesetzt. Der Kopplungslink bleibt auf der
// Basis (kein Session-Pfad). UI_BASE/UI_KEY stehen oben bei REPO_DIR.
console.log("\nKlickziel (Session-Deep-Link)");

/** Projekt-Kontext wie im Container — daraus leitet linkBase die Basis ab. */
const UI_ENV = {
  NTFY_URL: SINK_URL,
  NTFY_TOKEN: "tk_test",
  NTFY_PAIR_BUTTON: "0",
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

// e) Kopplungs-Button: weiterhin <basis>/auth/connect/<code> ohne Session-Pfad.
{
  resetSharedState();
  const { stub, published, pairCalls, pairActions } = pairFetchStub();
  const n = createNtfyNotifier({
    env: { ...UI_ENV, NTFY_PAIR_BUTTON: "1", OPENCODE_SERVER_PASSWORD: "pw_test" },
    apiPassword: "pw_test",
    state: freshState(),
    fetch: stub,
    log: () => {},
  });
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(published.length, 1, "die Meldung geht raus");
  eq(pairCalls().length, 1, "der Kopplungscode wird erzeugt");
  eq(pairActions().length, 1, "die Koppeln-Aktion hängt an der Meldung");
  eq(
    pairActions()[0]?.url,
    `${UI_BASE}/auth/connect/code_1`,
    "der Kopplungslink bleibt auf der Basis"
  );
  ok(
    !String(pairActions()[0]?.url ?? "").includes("/server/"),
    "der Kopplungslink trägt keinen Session-Pfad"
  );
  eq(
    published[0]?.click,
    `${UI_BASE}/server/${UI_KEY}/session/${SESSION}`,
    "das Klickziel derselben Meldung ist der Deep-Link"
  );
  resetSharedState();
}

// f) Eine nicht auswertbare Basis: kein Absturz, kein kaputter Link — es bleibt
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

// ── 36. Zustandsdatei: Zuordnung und Leseposition überleben den Neustart ──
//
// Ein Dienst-Neustart verliert den In-Memory-Zustand: im Protokoll stand danach
// „aktion: no-session“, eine Handy-Antwort fand ihre Session nicht mehr. Die
// wenigen nötigen Angaben liegen deshalb in NTFY_STATE_FILE. Hier wird der
// Neustart mit einem zweiten Zustandsspeicher (frischer Prozess) über derselben
// Datei simuliert: die Antwort muss in der richtigen Session landen und der
// Strom an der gespeicherten Leseposition ansetzen (keine Doppelverarbeitung).
console.log("\nZustandsdatei (Neustart)");
{
  resetSharedState();
  const topic = "ai";
  const statePath = testPath("ntfy-state-test.json");
  rmSync(statePath, { force: true });
  rmSync(`${statePath}.tmp`, { force: true });

  // Erster „Prozess“: eine Meldung geht raus (Zuordnung) und die Brücke
  // schreibt ihre Leseposition fort.
  const store1 = createStateStore({ path: statePath, log: () => {} });
  const runtime1 = { sentIds: [], processedIds: [], lastSessionID: "", lastActiveSession: "" };
  received.length = 0;
  const notifier1 = makeNotifier({ replyState: runtime1, stateStore: store1 });
  await notifier1.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  notifier1.markWorking(SESSION);
  await notifier1.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung des ersten Prozesses geht raus");
  eq(store1.get(topic, "lastSessionId"), SESSION, "die Zuordnung landet im Zustandsspeicher");

  const calls1 = [];
  const started1 = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: topic },
    runtime: runtime1,
    client: { session: { prompt: async () => ({}) } },
    publish: async () => true,
    // keepalive-Zeile: die Leseposition rückt weiter, ohne dass eine Nachricht
    // verarbeitet werden muss; der Strom endet danach wieder.
    fetch: streamFetch(calls1, ['{"id":"a3BxY9zQn12","event":"keepalive"}\n']),
    stateStore: store1,
    apiPassword: "",
    reconnectMs: 20,
    log: () => {},
  });
  ok(
    await until(() => runtime1.replySince === "a3BxY9zQn12"),
    "die Brücke schreibt ihre Leseposition fort"
  );
  started1.stop();
  await until(() => runtime1.replyPromise === null);
  store1.flush(true);
  eq(store1.get(topic, "since"), "a3BxY9zQn12", "die Leseposition steht im Zustandsspeicher");
  received.length = 0;

  // „Neustart“: neuer Speicher über derselben Datei, dazu ein leerer
  // Laufzeit-Zustand — genau so sieht es nach einem Prozess-Neustart aus.
  const store2 = createStateStore({ path: statePath, log: () => {} });
  eq(store2.get(topic, "lastSessionId"), SESSION, "die Zuordnung überlebt in der Datei");
  eq(store2.get(topic, "since"), "a3BxY9zQn12", "die Leseposition überlebt in der Datei");
  eq(store2.writtenHere(topic), false, "der neue Prozess hat das Topic noch nicht geschrieben");

  const runtime2 = { sentIds: [], processedIds: [], lastSessionID: "", lastActiveSession: "" };
  const calls2 = [];
  const prompts2 = [];
  const started2 = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: topic },
    runtime: runtime2,
    client: {
      session: {
        prompt: async (arg) => {
          prompts2.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    fetch: streamFetch(calls2, [
      '{"id":"msg_neu2","event":"message","title":"Nutzerantwort","message":"Bitte weitermachen."}\n',
    ]),
    stateStore: store2,
    apiPassword: "",
    reconnectMs: 20,
    log: () => {},
  });
  ok(await until(() => calls2.length >= 1), "die neue Brücke verbindet sich");
  ok(
    String(calls2[0]?.url).includes("since=a3BxY9zQn12"),
    `sie liest an der gespeicherten Position weiter (war: ${calls2[0]?.url})`
  );
  ok(await until(() => prompts2.length === 1), "die Handy-Antwort wird zugestellt");
  eq(prompts2[0]?.sessionID, SESSION, "die persistierte Zuordnung liefert die richtige Session");
  eq(prompts2[0]?.text, "Bitte weitermachen.", "der Text kommt unverändert an");
  started2.stop();
  await until(() => runtime2.replyPromise === null);

  // Ohne Datei ändert sich nichts: nichts persistiert, nichts gefunden.
  const emptyPath = testPath("ntfy-state-test-leer.json");
  rmSync(emptyPath, { force: true });
  const store3 = createStateStore({ path: emptyPath, log: () => {} });
  eq(store3.get(topic, "lastSessionId"), "", "eine fehlende Datei ergibt einen leeren Eintrag");
  eq(store3.writtenHere(topic), false, "ohne Datei wurde nichts geschrieben");

  // Kaputte Datei: kein Fehler, es wird wie ohne Datei gearbeitet.
  const brokenPath = testPath("ntfy-state-test-kaputt.json");
  await (await import("node:fs/promises")).writeFile(brokenPath, "{kaputt");
  const broken = createStateStore({ path: brokenPath, log: () => {} });
  eq(broken.get(topic, "since"), "", "eine kaputte Datei wird wie eine leere behandelt");
  ok(broken.set(topic, { lastSessionId: SESSION }), "danach lässt sie sich wieder beschreiben");
  broken.flush(true);
  eq(
    createStateStore({ path: brokenPath, log: () => {} }).get(topic, "lastSessionId"),
    SESSION,
    "der neue Stand steht in der Datei"
  );

  // Halb gefüllte Datei: vorhandene Felder gelten, fehlende bleiben leer,
  // unsinnige Einträge sind kein Fehler.
  const partialPath = testPath("ntfy-state-test-teil.json");
  await (await import("node:fs/promises")).writeFile(
    partialPath,
    JSON.stringify({ topics: { ai: { lastSessionId: SESSION }, zweit: "quatsch" } })
  );
  const partial = createStateStore({ path: partialPath, log: () => {} });
  eq(partial.get(topic, "lastSessionId"), SESSION, "vorhandene Felder werden übernommen");
  eq(partial.get(topic, "since"), "", "fehlende Felder bleiben leer");
  eq(partial.get("zweit", "lastSessionId"), "", "unsinnige Einträge sind kein Fehler");

  // Gedrosseltes Schreiben: höchstens einmal je Sekunde — der letzte Stand
  // kommt mit flush() (bzw. beim Ende der Brücke) in die Datei.
  const throttlePath = testPath("ntfy-state-test-drossel.json");
  rmSync(throttlePath, { force: true });
  let clock = 1_000_000;
  const throttle = createStateStore({ path: throttlePath, now: () => clock, log: () => {} });
  throttle.set(topic, { lastSessionId: "ses_erste1" });
  clock += 100;
  throttle.set(topic, { lastSessionId: "ses_zweite2" });
  eq(
    createStateStore({ path: throttlePath, log: () => {} }).get(topic, "lastSessionId"),
    "ses_erste1",
    "innerhalb der Drosselung wird nicht geschrieben"
  );
  throttle.flush(true);
  eq(
    createStateStore({ path: throttlePath, log: () => {} }).get(topic, "lastSessionId"),
    "ses_zweite2",
    "nach dem erzwungenen Schreiben steht der letzte Stand in der Datei"
  );

  // Aufräumen: die Testdateien haben im Betrieb nichts zu suchen.
  for (const path of [statePath, emptyPath, brokenPath, partialPath, throttlePath]) {
    rmSync(path, { force: true });
  }
  rmSync(`${statePath}.tmp`, { force: true });
}

// ── 37. Sessions per Titel stummschalten (NTFY_IGNORE_TITLES) ─────────────
//
// Manche Sessions sind selbst Diagnose (z. B. „Hestia Ausfall-Probe“) und
// sollen keine Meldungen erzeugen. NTFY_IGNORE_TITLES nimmt kommagetrennte
// Teilzeichenketten (getrimmt, Groß-/Kleinschreibung egal): passt eine davon
// auf den Titel der Session, geht gar nichts raus — weder Frage noch Fertig
// noch Fehler. Leer/nicht gesetzt bleibt das Verhalten unverändert.
// ── Letzte bekannte Basis (Kopplungs-Ersatzadresse, lastBase) ────────────
//
// Meldungen ohne Session (z. B. „Antwort an Agenten weitergegeben“, „Antwort
// ohne Ziel-Session“) haben keine aus den Sitzungs-Metadaten abgeleitete Basis.
// Statt der allgemeinen NTFY_FALLBACK_URL nutzen sie die zuletzt abgeleitete
// Workspace-Basis — nur auf dieser App-Adresse gilt das Kopplungs-Cookie. Die
// Basis liegt in NTFY_STATE_FILE und überlebt damit einen Dienst-Neustart;
// kaputte oder fehlende Werte ändern nur die Basis, nie die Meldung.
console.log("\nLetzte bekannte Basis");
{
  const statePath = testPath("ntfy-lastbase-test.json");
  const FALLBACK = "https://fallback.test";
  rmSync(statePath, { force: true });
  rmSync(`${statePath}.tmp`, { force: true });
  const store = createStateStore({ path: statePath, log: () => {} });
  const env = { ...UI_ENV, NTFY_PAIR_BUTTON: "1", NTFY_FALLBACK_URL: `${FALLBACK}/` };
  const notify = (extra) =>
    createNtfyNotifier({
      env,
      apiPassword: "pw_test",
      state: freshState(),
      stateStore: store,
      fetch: extra.fetch,
      log: () => {},
    });

  // (b) Ohne vorherige Meldung greift weiterhin NTFY_FALLBACK_URL.
  resetSharedState();
  {
    const { stub, pairCalls, pairActions } = pairFetchStub();
    const n = notify({ fetch: stub });
    await n.send({ title: "↩️ Antwort an Agenten weitergegeben", message: "Text" });
    eq(store.get("ai", "lastBase"), "", "ohne Meldung mit Session wird keine Basis gemerkt");
    eq(pairCalls().length, 1, "der Kopplungscode wird trotzdem erzeugt");
    eq(
      pairActions()[0]?.url,
      `${FALLBACK}/auth/connect/code_1`,
      "ohne gemerkte Basis bleibt es beim Rückfall (NTFY_FALLBACK_URL)"
    );
  }

  // (a) Eine Meldung mit Session merkt die abgeleitete Basis; die nächste
  //     Meldung ohne Session koppelt genau auf dieser Basis.
  resetSharedState();
  {
    const { stub, published, pairActions, pairCalls } = pairFetchStub();
    const n = notify({ fetch: stub });
    await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
    n.markWorking(SESSION);
    await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
    eq(published.length, 1, "die Meldung mit Session geht raus");
    eq(store.get("ai", "lastBase"), UI_BASE, "die abgeleitete Basis steht im Zustandsspeicher");
    eq(pairActions()[0]?.url, `${UI_BASE}/auth/connect/code_1`, "sie koppelt auf ihrer Basis");

    await n.send({ title: "↩️ Antwort an Agenten weitergegeben", message: "Text" });
    eq(published.length, 2, "die Meldung ohne Session geht raus");
    eq(
      pairActions(1)[0]?.url,
      `${UI_BASE}/auth/connect/code_1`,
      "die Meldung ohne Session koppelt auf der zuletzt gemerkten Basis"
    );
    eq(pairCalls().length, 1, "beide Meldungen nutzen dieselbe Basis (ein Code)");
  }

  // (c) Neustart: neuer Zustandsspeicher über derselben Datei.
  store.flush(true);
  resetSharedState();
  {
    const store2 = createStateStore({ path: statePath, log: () => {} });
    eq(store2.get("ai", "lastBase"), UI_BASE, "die Basis überlebt in der Datei");
    const { stub, pairActions } = pairFetchStub();
    const n = createNtfyNotifier({
      env,
      apiPassword: "pw_test",
      state: freshState(),
      stateStore: store2,
      fetch: stub,
      log: () => {},
    });
    await n.send({ title: "⚠️ Antwort ohne Ziel-Session", message: "Text" });
    eq(
      pairActions()[0]?.url,
      `${UI_BASE}/auth/connect/code_1`,
      "auch nach dem Neustart koppelt sie auf der gemerkten Basis"
    );
  }
  rmSync(statePath, { force: true });

  // Robustheit: ein kaputter Zustandsspeicher fällt auf den Rückfall zurück und
  // lässt die Meldung unberührt.
  {
    const broken = {
      get() {
        throw new Error("kaputt");
      },
      set() {},
      flush() {},
    };
    const sent = [];
    const n = createNtfyNotifier({
      env: { ...env, NTFY_PAIR_BUTTON: "0" },
      state: freshState(),
      stateStore: broken,
      fetch: async (input, init = {}) => {
        sent.push(JSON.parse(String(init.body ?? "{}")));
        return { ok: true, status: 200, json: async () => ({ id: "msg_kaputt1" }) };
      },
      log: () => {},
    });
    eq(n.linkFor(""), `${FALLBACK}/`, "ein kaputter Speicher fällt auf den Rückfall zurück");
    const raus = await n.send({ title: "↩️ Antwort an Agenten weitergegeben", message: "Text" });
    eq(raus, true, "die Meldung ohne Session geht trotzdem raus");
    eq(sent[0]?.title, "↩️ Antwort an Agenten weitergegeben", "ihr Titel bleibt unverändert");
  }
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

console.log("\nStummschalten per Titel");
{
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_IGNORE_TITLES: "  ausfall-probe , " },
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

// ── 38. Klickziel-Stil (NTFY_LINK_STYLE) ─────────────────────────────────
//
// Übergangslösung: "base" setzt das Klickziel (click-Feld und die Aktion
// „Session öffnen") auf die Basis, also die Startseite der Oberfläche.
// Standard bleibt "session" = Session-Deep-Link. Der Kopplungsknopf hängt
// weiterhin an der Basis (siehe Kopplungslink-Tests).
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
// einheitlich für den Versand UND die Antwort-Brücke (die hört auf demselben
// Topic). Ohne bekannten Arbeitsbereich greift wieder NTFY_TOPIC.
console.log("\nTopic je Arbeitsbereich");
{
  // a) Ohne Prefix: alles unverändert.
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_PAIR_BUTTON: "0",
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
      NTFY_PAIR_BUTTON: "0",
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
      NTFY_PAIR_BUTTON: "0",
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
      NTFY_PAIR_BUTTON: "0",
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
  // d) Antwort-Brücke: sie muss auf demselben Topic hören (Strom-URL und
  //    Diagnosezeile beim Start) — sonst fände keine Antwort mehr zurück.
  resetSharedState();
  const runtime = replyRuntime();
  const logPath = testPath("ntfy-topic-prefix-test.log");
  rmSync(logPath, { force: true });
  const readLog = () => {
    try {
      return readFileSync(logPath, "utf8");
    } catch {
      return "";
    }
  };
  const calls = [];
  const started = startReplyBridge({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_TOPIC_PREFIX: "oc",
      NTFY_WORKSPACE: "server-management",
      NTFY_REPLY_LOG: logPath,
    },
    runtime,
    directory: `${REPO_DIR}/workspaces/infrastructure/server-management`,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: openStreamFetch(calls),
    reconnectMs: 20,
    apiPassword: "",
    log: () => {},
  });
  ok(await until(() => calls.length === 1), "die Brücke verbindet sich");
  eq(started.bridge?.topic, "oc-server-management", "die Brücke hört auf dem Topic des Arbeitsbereichs");
  ok(
    decodeURIComponent(String(calls[0]?.url)).includes("/oc-server-management/json"),
    `der Strom wird mit dem Topic des Arbeitsbereichs abgefragt (war: ${calls[0]?.url})`
  );
  ok(
    await until(() => readLog().includes("topic=oc-server-management")),
    "die Diagnosezeile beim Brücken-Start nennt das tatsächlich verwendete Topic"
  );
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
  rmSync(logPath, { force: true });
}
{
  // Gegenprobe: ohne Prefix bleibt die Brücke bei NTFY_TOPIC.
  resetSharedState();
  const runtime = replyRuntime();
  const calls = [];
  const started = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    directory: `${REPO_DIR}/workspaces/infrastructure/server-management`,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: openStreamFetch(calls),
    reconnectMs: 20,
    apiPassword: "",
    log: () => {},
  });
  ok(await until(() => calls.length === 1), "die Brücke verbindet sich");
  ok(
    decodeURIComponent(String(calls[0]?.url)).includes("/ai/json"),
    `ohne Prefix bleibt es beim Topic 'ai' (war: ${calls[0]?.url})`
  );
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}
{
  // e) Die Aktiv-Zeile des Plugins nennt das tatsächlich verwendete Topic
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
        NTFY_PAIR_BUTTON: "0",
        NTFY_REPLY_ENABLED: "0",
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
      NTFY_PAIR_BUTTON: "0",
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
      NTFY_PAIR_BUTTON: "0",
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
        NTFY_PAIR_BUTTON: "0",
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
    `${iconBase}/homeassistant.png`,
    "das icon zeigt weiter auf den Arbeitsbereich des Sitzungs-Verzeichnisses"
  );
  eq(mit.body.icon, ohne.body.icon, "NTFY_TOPIC_NAME ändert das icon-Feld nicht");
  eq(mit.body.title, ohne.body.title, "NTFY_TOPIC_NAME ändert den Titel nicht");
  ok(
    String(mit.body.title ?? "").startsWith("[homeassistant]"),
    `die Titel-Kennung kommt weiter aus dem Sitzungs-Verzeichnis (war: ${mit.body.title})`
  );
}
{
  // d) Brücke und Notifier leiten dasselbe Topic ab: gleiche Env, gleiches
  //    Verzeichnis — genau die Verdrahtung aus createPluginRuntime. Die Brücke
  //    muss auf dem Topic hören, in das der Notifier sendet, sonst findet keine
  //    Antwort mehr zurück.
  const env = {
    NTFY_URL: SINK_URL,
    NTFY_TOKEN: "tk_test",
    NTFY_TOPIC: "ai",
    NTFY_TOPIC_PREFIX: "oc",
    NTFY_TOPIC_NAME: "infrastructure",
  };
  const notifier = createNtfyNotifier({
    env,
    directory: REPO_DIR,
    state: freshState(),
    log: () => {},
  });
  eq(notifier.topic, "oc-infrastructure", "der Notifier leitet das Topic ab");
  resetSharedState();
  const runtime = replyRuntime();
  const calls = [];
  const started = startReplyBridge({
    env,
    runtime,
    directory: REPO_DIR,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: openStreamFetch(calls),
    reconnectMs: 20,
    apiPassword: "",
    log: () => {},
  });
  ok(await until(() => calls.length === 1), "die Brücke verbindet sich");
  eq(started.bridge?.topic, "oc-infrastructure", "die Brücke hört auf <prefix>-<name>");
  eq(started.bridge?.topic, notifier.topic, "Brücke und Notifier nutzen dasselbe Topic");
  ok(
    decodeURIComponent(String(calls[0]?.url)).includes("/oc-infrastructure/json"),
    `der Strom wird mit demselben Topic abgefragt (war: ${calls[0]?.url})`
  );
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 40. Symbol je Meldung (NTFY_ICON_BASE) ────────────────────────────────
//
// Ist NTFY_ICON_BASE gesetzt und der Arbeitsbereich bekannt, trägt die Meldung
// das ntfy-Feld `icon` = "<basis>/<workspace>.png" (JSON-Feld `icon`, als
// Header `Icon`; die Apps zeigen das Bild als Benachrichtigungssymbol). Ohne
// die Basis oder ohne bekannten Arbeitsbereich entfällt das Feld — dann bleibt
// das Verhalten unverändert.
console.log("\nSymbol (icon)");
const ICON_BASE =
  "https://raw.githubusercontent.com/DasCarstel/opencode-coder-env/master/assets/ntfy-icons";
{
  const n = makeNotifier({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0", NTFY_ICON_BASE: ICON_BASE },
  });
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: info(SESSION) } });
  n.markWorking(SESSION);
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "die Meldung geht raus");
  eq(
    received[0]?.body?.icon,
    `${ICON_BASE}/server-management.png`,
    "icon zeigt auf das PNG des Arbeitsbereichs"
  );
}
{
  // NTFY_WORKSPACE genügt, wenn die Session kein Verzeichnis mitbringt; ein
  // abschließender Slash der Basis erzeugt kein doppeltes "//".
  const n = makeNotifier({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_PAIR_BUTTON: "0",
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
    `${ICON_BASE}/server-management.png`,
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_PAIR_BUTTON: "0", NTFY_ICON_BASE: ICON_BASE },
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
  ok(TEST_STATE_FILE.startsWith(TEST_DIR), "die Test-Zustandsdatei liegt im Temp-Verzeichnis");
  ok(!TEST_STATE_FILE.includes(livePfad), "die Test-Zustandsdatei ist nicht die des Dienstes");
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

  // Der Zustand der Test-Instanzen landet im Temp-Verzeichnis.
  const n = makeNotifier({
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ id: "aB3xY9zQk12" }) }),
  });
  eq(n.topic, "ai", "Test-Notifier nutzt wie bisher das Topic ai");
  received.length = 0;
  ok(await n.sendOnce(SESSION, "probe", { title: "Probe", message: "Probe" }), "Probe-Meldung geht raus");
  ok(await until(() => existsSync(TEST_STATE_FILE)), "die Zustandsdatei entsteht im Temp-Verzeichnis");
  ok(
    await until(() => readFileSync(TEST_STATE_FILE, "utf8").includes("lastSessionId")),
    "die Zustandsdatei trägt die Testzuordnung"
  );
  received.length = 0;
}

// ── 42. since-Zeiger: nur gültige Positionen ──────────────────────────────
console.log("\nsince-Zeiger");
{
  ok(isValidSinceId("aB3xY9zQk12"), "echte ntfy-ID gilt");
  ok(isValidSinceId("1700000000"), "Unix-Sekunden gelten als Position");
  ok(!isValidSinceId("msg_user9"), "Fixture-Wert msg_… gilt nicht (Vorfall vom 17.10.)");
  ok(!isValidSinceId("all"), "'all' gilt nicht (das wäre der ganze Verlauf)");
  ok(!isValidSinceId("0"), "'0' gilt nicht");
  ok(!isValidSinceId("-1"), "'-1' gilt nicht");
  ok(!isValidSinceId("none"), "'none' gilt nicht");
  ok(!isValidSinceId(""), "leer gilt nicht");
  ok(!isValidSinceId("kurz"), "zu kurze Werte gelten nicht");
}
{
  resetSharedState();
  const zustand = testPath("since-ungueltig.json");
  writeFileSync(
    zustand,
    JSON.stringify({ version: 1, topics: { ai: { since: "msg_user9" } } })
  );
  const store = createStateStore({
    path: zustand,
    env: { NTFY_STATE_FILE: zustand },
    throttleMs: 0,
  });
  const warnungen = [];
  const calls = [];
  const started = startReplyBridge({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_REPLY_LOG: testPath("since-warnung.log"),
    },
    runtime: replyRuntime(),
    stateStore: store,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: openStreamFetch(calls, ['{"id":"aB3xY9zQk12","event":"keepalive"}\n']),
    reconnectMs: 20,
    log: (zeile) => warnungen.push(String(zeile)),
  });
  ok(await until(() => calls.length >= 1), "die Brücke verbindet sich");
  const url = decodeURIComponent(String(calls[0]?.url ?? ""));
  ok(!url.includes("msg_user9"), "der ungültige Zeiger wird nicht als since benutzt");
  ok(/[?&]since=\d{9,}/.test(url), "stattdessen startet die Brücke ab jetzt (Unix-Sekunden)");
  ok(
    warnungen.some((zeile) => zeile.includes("since ungueltig, starte ab jetzt")),
    "die Warnzeile steht im Protokoll"
  );
  await until(() => readFileSync(testPath("since-warnung.log"), "utf8").includes("since ungueltig"));
  ok(
    readFileSync(testPath("since-warnung.log"), "utf8").includes("since ungueltig"),
    "die Warnzeile steht auch in der Diagnose"
  );
  // Nur gültige Werte werden persistiert.
  ok(await until(() => isValidSinceId(store.get("ai", "since"))), "persistiert wird nur eine gültige Position");
  started.stop();
  await until(() => replyRuntime().replyPromise === null);
  resetSharedState();
}
{
  resetSharedState();
  const zustand = testPath("since-gueltig.json");
  writeFileSync(
    zustand,
    JSON.stringify({ version: 1, topics: { ai: { since: "1700000000" } } })
  );
  const store = createStateStore({
    path: zustand,
    env: { NTFY_STATE_FILE: zustand },
    throttleMs: 0,
  });
  const calls = [];
  const started = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime: replyRuntime(),
    stateStore: store,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: openStreamFetch(calls, []),
    reconnectMs: 20,
    log: () => {},
  });
  ok(await until(() => calls.length >= 1), "die Brücke verbindet sich");
  ok(
    String(calls[0]?.url ?? "").includes("since=1700000000"),
    "ein gültiger Zeiger aus der Zustandsdatei wird übernommen"
  );
  started.stop();
  await until(() => replyRuntime().replyPromise === null);
  resetSharedState();
}

// ── 43. Wasserstand: nur neue Nachrichten ─────────────────────────────────
console.log("\nWasserstand (Zeitfenster)");
{
  resetSharedState();
  const jetzt = Math.floor(Date.now() / 1000);
  const zustand = testPath("wasserstand.json");
  writeFileSync(
    zustand,
    JSON.stringify({
      version: 1,
      topics: { ai: { lastHandledTime: String(jetzt), lastSessionId: SESSION } },
    })
  );
  const store = createStateStore({
    path: zustand,
    env: { NTFY_STATE_FILE: zustand },
    throttleMs: 0,
  });
  const runtime = replyRuntime();
  // Eigene Meldung, deren Wortlaut später erneut aus dem Topic kommt. Der
  // Sink des Testlaufs wird von einem früheren Test geschlossen — diese Prüfung
  // arbeitet deshalb mit injiziertem fetch (wie alle anderen Meldungstests).
  const notifier = makeNotifier({
    replyState: runtime,
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ id: "aB3xY9zQk12" }) }),
  });
  received.length = 0;
  ok(await notifier.send({ title: "Hinweis", message: "Bitte Variante B umsetzen", priority: 2 }), "eigene Meldung geht raus");
  received.length = 0;
  received.length = 0;

  const prompts = [];
  const published = [];
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    stateStore: store,
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async (payload) => {
      published.push(payload);
      return true;
    },
    log: () => {},
  });

  // (a) alte Nachricht unter dem Wasserstand → skip, kein Prompt.
  const alt = await bridge.handleMessage({
    id: "a11b22c33d",
    title: "Nutzerantwort",
    message: "uralt",
    time: jetzt - 600,
  });
  eq(alt.action, "skipped", "alte Nachricht wird verworfen");
  eq(alt.reason, "zu-alt", "Grund ist zu-alt");
  eq(prompts.length, 0, "kein Prompt für Verlauf");
  eq(published.length, 0, "keine Veröffentlichung für Verlauf");

  // (b) neuere Nachricht → Prompt.
  const neu = await bridge.handleMessage({
    id: "b11b22c33d",
    title: "Nutzerantwort",
    message: "frisch",
    time: jetzt + 5,
  });
  eq(neu.action, "injected", "neuere Nachricht wird eingespeist");
  eq(prompts[0]?.text, "frisch", "der neue Text kommt als Prompt an");

  // (c) dritte Nachricht binnen einer Minute → Drossel.
  const zweite = await bridge.handleMessage({
    id: "c11b22c33d",
    title: "Nutzerantwort",
    message: "nochmal",
    time: jetzt + 6,
  });
  eq(zweite.action, "injected", "die zweite Einspeisung ist noch erlaubt");
  const dritte = await bridge.handleMessage({
    id: "d11b22c33d",
    title: "Nutzerantwort",
    message: "zu viel",
    time: jetzt + 7,
  });
  eq(dritte.action, "skipped", "die dritte Nachricht binnen einer Minute wird gedrosselt");
  eq(dritte.reason, "drossel", "Grund ist drossel");
  eq(prompts.length, 2, "die Drossel verhindert jede weitere Einspeisung");
  eq(bridge.watermark(), jetzt + 7, "der Wasserstand läuft auch bei verworfenen Nachrichten weiter");

  // (e) eigener Wortlaut → eigener-text.
  const eigen = await bridge.handleMessage({
    id: "e11b22c33d",
    title: "Nutzernachricht",
    message: "Bitte Variante B umsetzen",
    time: jetzt + 8,
  });
  eq(eigen.action, "skipped", "eigener Wortlaut wird nicht eingespeist");
  eq(eigen.reason, "eigener-text", "Grund ist eigener-text");
  eq(prompts.length, 2, "kein weiterer Prompt durch den eigenen Text");
  eq(published.length, 2, "keine weitere Veröffentlichung durch den eigenen Text");
}
{
  // (d) frischer Start ohne Wasserstand: der alte Verlauf bleibt draußen.
  resetSharedState();
  const jetzt = Math.floor(Date.now() / 1000);
  const zustand = testPath("wasserstand-frisch.json");
  writeFileSync(zustand, JSON.stringify({ version: 1, topics: { ai: {} } }));
  const store = createStateStore({
    path: zustand,
    env: { NTFY_STATE_FILE: zustand },
    throttleMs: 0,
  });
  const prompts = [];
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime: replyRuntime(),
    stateStore: store,
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    log: () => {},
  });
  ok(bridge.watermark() >= jetzt - 2, "ohne gespeicherten Wert steht der Wasserstand auf jetzt");
  ok(bridge.watermark() > 0, "der Wasserstand ist nie 0");
  const alt = await bridge.handleMessage({
    id: "f11b22c33d",
    title: "Nutzerantwort",
    message: "uralt",
    time: jetzt - 3600,
  });
  eq(alt.action, "skipped", "frischer Start: alter Verlauf wird nicht eingespeist");
  eq(alt.reason, "zu-alt", "auch hier ist der Grund zu-alt");
  eq(prompts.length, 0, "kein Prompt nach dem frischen Start");
}

// ── 44. Eigene Meldungen über Neustarts hinweg erkennen ───────────────────
console.log("\nEigene Meldungen (persistierte IDs)");
{
  resetSharedState();
  const jetzt = Math.floor(Date.now() / 1000);
  const zustand = testPath("eigene-ids.json");
  writeFileSync(
    zustand,
    JSON.stringify({
      version: 1,
      topics: {
        ai: {
          sentIds: JSON.stringify(["aB3xY9zQk12"]),
          lastHandledTime: String(jetzt),
          lastSessionId: SESSION,
        },
      },
    })
  );
  const store = createStateStore({
    path: zustand,
    env: { NTFY_STATE_FILE: zustand },
    throttleMs: 0,
  });
  // Leerer Speicher: wie direkt nach einem Dienst-Neustart.
  const runtime = { sentIds: [], processedIds: [], lastSessionID: SESSION };
  const prompts = [];
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
    runtime,
    stateStore: store,
    apiPassword: "",
    client: {
      session: {
        prompt: async (arg) => {
          prompts.push(arg);
          return {};
        },
      },
    },
    publish: async () => true,
    log: () => {},
  });

  const byId = await bridge.handleMessage({
    id: "aB3xY9zQk12",
    title: "Nutzertext",
    message: "selbst gesendet",
    time: jetzt + 5,
  });
  eq(byId.action, "skipped", "ID aus dem persistierten Puffer → skipped");
  eq(byId.reason, "eigene-id", "Grund ist eigene-id");
  eq(prompts.length, 0, "die eigene Meldung löst keinen Prompt aus");

  const byTitle = await bridge.handleMessage({
    id: "b22c33d44e",
    title: "[server-management] ✅ Fertig — server-management",
    message: "Bin fertig.",
    time: jetzt + 6,
  });
  eq(byTitle.action, "ignored", "eigener Titel → ignored");
  eq(byTitle.reason, "eigener-titel", "Grund ist eigener-titel");
  eq(prompts.length, 0, "auch der eigene Titel löst keinen Prompt aus");

  // Titel ohne Präfix, aber Text wie zuletzt gesendet.
  const notifier = makeNotifier({
    replyState: runtime,
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ id: "aB3xY9zQk12" }) }),
  });
  received.length = 0;
  await notifier.send({ title: "Hinweis", message: "Und danach C", priority: 2 });
  received.length = 0;
  const eigen = await bridge.handleMessage({
    id: "c22c33d44e",
    title: "Nutzernachricht",
    message: "Und danach C",
    time: jetzt + 7,
  });
  eq(eigen.action, "skipped", "Text wie zuletzt gesendet → skipped");
  eq(eigen.reason, "eigener-text", "Grund ist eigener-text");
  eq(prompts.length, 0, "kein Prompt für den eigenen Wortlaut");
}

// ── 45. Offene Fragen: nur Text nach dem Stellen ──────────────────────────
console.log("\nOffene Frage (Zeitfenster)");
{
  resetSharedState();
  const jetzt = Math.floor(Date.now() / 1000);
  const zustand = testPath("frage-zeit.json");
  writeFileSync(
    zustand,
    JSON.stringify({
      version: 1,
      topics: {
        ai: {
          openQuestionId: "frm_zeit1",
          openQuestionAt: String(jetzt),
          lastHandledTime: String(jetzt - 100),
          lastSessionId: SESSION,
        },
      },
    })
  );
  const forms = [openForm("frm_zeit1", [choiceField])];
  const { bridge, prompts, posts, deletes } = makeFormBridge({
    forms,
    env: { NTFY_STATE_FILE: zustand, NTFY_URL: SINK_URL },
  });

  const alt = await bridge.handleMessage({
    id: "a33d44e55f",
    title: "Nutzerantwort",
    message: "2",
    time: jetzt - 30,
  });
  eq(alt.action, "skipped", "Nachricht vor der Frage wird nicht verwendet");
  eq(alt.reason, "zu-alt-fuer-frage", "Grund ist zu-alt-fuer-frage");
  eq(posts().length, 0, "kein Formularschreiben");
  eq(deletes().length, 0, "die Frage bleibt offen (kein Abbruch)");
  eq(prompts.length, 0, "keine Einspeisung");
  eq(forms.length, 1, "die Frage steht weiterhin offen");

  const neu = await bridge.handleMessage({
    id: "b33d44e55f",
    title: "Nutzerantwort",
    message: "2",
    time: jetzt + 10,
  });
  eq(neu.action, "replied", "Nachricht nach der Frage wird eingetragen");
  eq(posts().length, 1, "genau ein Reply-POST");
  eq(prompts.length, 0, "kein Prompt — die Frage wurde beantwortet");
}
{
  // Altbestand: offene Frage ohne gespeicherten Zeitpunkt → keine Automatik.
  resetSharedState();
  const jetzt = Math.floor(Date.now() / 1000);
  const zustand = testPath("frage-ohne-zeit.json");
  writeFileSync(
    zustand,
    JSON.stringify({
      version: 1,
      topics: { ai: { openQuestionId: "frm_ohne", lastHandledTime: String(jetzt - 100) } },
    })
  );
  const { bridge, posts, prompts } = makeFormBridge({
    forms: [openForm("frm_ohne", [choiceField])],
    env: { NTFY_STATE_FILE: zustand, NTFY_URL: SINK_URL },
  });
  const result = await bridge.handleMessage({ id: "c33d44e55f", message: "2" });
  eq(posts().length, 0, "ohne Zeitpunkt wird nichts eingetragen");
  eq(result.action, "skipped", "die Nachricht wird nicht verwendet");
  eq(prompts.length, 0, "keine Einspeisung ohne Zeitpunkt");
}

console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
