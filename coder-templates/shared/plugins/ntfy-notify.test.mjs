// Tests für ntfy-notify.js — laufen ohne OpenCode-Server:
//   node coder-templates/shared/plugins/ntfy-notify.test.mjs
//
// Startet einen lokalen HTTP-Sink, spielt Events ein und prüft, was tatsächlich
// als ntfy-Publish rausgeht (Topic, Titel, Priorität, Body, Click-URL).
// Zusätzlich wird die V2-Anbindung (setup → ctx.event.subscribe) mit einem
// Mock-Context getestet.

import http from "node:http";
import {
  createNtfyNotifier,
  createPluginRuntime,
  isServiceProcess,
  resetSharedState,
  subpathFromDirectory,
} from "./ntfy-notify.js";

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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", ...(extra.env ?? {}) },
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
  eq(body.title, "✅ Fertig — server-management", "Titel mit Projekt");
  eq(body.priority, 3, "Priorität 3");
  ok(String(body.message).includes("Fertig: alle 3 Punkte umgesetzt."), "TLDR ist enthalten");
  ok(String(body.message).includes("Ablauf prüfen"), "Session-Titel ist enthalten");
  eq(
    body.click,
    "https://herdr--infrastructure--carstenmueller2002-coder.mueller-nas.de",
    "Click-URL zeigt auf die Coder-App"
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
  eq(body.title, "❓ Frage — server-management", "Frage-Titel");
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
  eq(perm.title, "🔐 Freigabe — server-management", "Freigabe-Titel");
  eq(perm.priority, 4, "Priorität 4");
  ok(String(perm.message).includes("rm -rf /tmp/x"), "Befehl enthalten");

  received.length = 0;
  await n.handleEvent({
    type: "session.error",
    properties: { sessionID: SESSION, error: { message: "Provider nicht erreichbar" } },
  });
  const err = received[0]?.body ?? {};
  eq(err.title, "⚠️ Fehler — server-management", "Fehler-Titel");
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
  eq(body.click, "https://coder.mueller-nas.de/", "URL-Fallback");
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
  eq(received[0]?.body?.title, "✅ Fertig — server-management", "Titel aus session-Objekt");
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
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
  const a = createNtfyNotifier({ env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" }, log: () => {} });
  const b = createNtfyNotifier({ env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" }, log: () => {} });
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
  const envFile = "/tmp/ntfy-notify-test.env";
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
  eq(body.title, "✅ Fertig — server-management", "Projekt-Label aus location.directory");
  ok(String(body.message).includes("Alles erledigt"), "TLDR stammt aus session.text.ended");
  eq(
    body.click,
    "https://herdr--infrastructure--carstenmueller2002-coder.mueller-nas.de",
    "Click-URL aus location.directory"
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
  eq(body.title, "❓ Frage — server-management", "Titel der Frage");
  eq(body.priority, 4, "Priorität 4");
  ok(String(body.message).includes("Weiter mit Phase 4?"), "Fragetext enthalten");
  ok(String(body.message).includes("Ja | Nein"), "Optionen enthalten");
}
{
  const n = makeNotifier();
  received.length = 0;
  await n.handleEvent(v2("form.created", { form: { schema: "x", nested: { weird: true } } }));
  eq(received.length, 1, "unbekannte Form-Struktur meldet trotzdem");
  ok(String(received[0]?.body?.message ?? "").length > 10, "Fallback-Body ist gefüllt");
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

sink.close();

console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
