// Tests für ntfy-notify.js — laufen ohne OpenCode-Server:
//   node coder-templates/shared/plugins/ntfy-notify.test.mjs
//
// Startet einen lokalen HTTP-Sink, spielt Events ein und prüft, was tatsächlich
// als ntfy-Publish rausgeht (Topic, Titel, Priorität, Body, Click-URL).
// Zusätzlich wird die V2-Anbindung (setup → ctx.event.subscribe) mit einem
// Mock-Context getestet.

import { readFileSync, rmSync } from "node:fs";
import http from "node:http";
import {
  createNtfyNotifier,
  createPluginRuntime,
  createReplyBridge,
  isServiceProcess,
  parseReplyStream,
  replyRuntime,
  resetSharedState,
  startReplyBridge,
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
  eq(body.title, "✅ Fertig — server-management", "Titel mit Projekt");
  eq(body.priority, 3, "Priorität 3");
  ok(String(body.message).includes("Fertig: alle 3 Punkte umgesetzt."), "TLDR ist enthalten");
  ok(String(body.message).includes("Ablauf prüfen"), "Session-Titel ist enthalten");
  eq(
    body.click,
    "https://49374--main--infrastructure--carstenmueller2002-coder.mueller-nas.de",
    "Click-URL zeigt auf die OpenCode-Port-App"
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
  const envFile = "/tmp/ntfy-notify-test.env";
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
    "https://49374--main--infrastructure--carstenmueller2002-coder.mueller-nas.de",
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
  eq(body.title, "❓ Frage — server-management", "Titel trotz fehlender data.sessionID");
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
  eq(received[0]?.body?.title, "✅ Fertig — server-management", "Titel beim Turn-Ende");
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
  eq(received[0]?.body?.title, "✅ Fertig — server-management", "Titel der Hauptsession");
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
    "https://49374--main--infrastructure--carstenmueller2002-coder.mueller-nas.de",
    "Klick-Link folgt dem Port-Agent-Workspace-Owner-Muster"
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
    "https://beispiel.test/session/42",
    "NTFY_SESSION_URL überschreibt den Klick-Link"
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

// ── 24. Antwort-Brücke: NDJSON-Abfrage (fetch gemockt) ───────────────────
console.log("\nAntwort-Brücke (Abfrage)");
{
  const requests = [];
  const runtime = { sentIds: [], lastSessionID: SESSION };
  let calls = 0;
  const fetchStub = async (input, init) => {
    requests.push({ url: String(input), init });
    calls += 1;
    // Erste Abfrage liefert Nachrichten, danach ist das Topic leer.
    const body =
      calls === 1
        ? [
            JSON.stringify({ id: "msg_keep", event: "keepalive" }),
            JSON.stringify({ id: "msg_a1", event: "message", title: "Antwort", message: "Weiter mit B" }),
            "kein json",
            JSON.stringify({ id: "msg_a2", event: "message", title: "Nachtrag", message: "Und danach C" }),
            "",
          ].join("\n")
        : "";
    return { ok: true, status: 200, text: async () => body };
  };
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    runtime,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    log: () => {},
  });

  const round = await bridge.pollOnce("1700000000");
  eq(round.messages.length, 2, "NDJSON wird zeilenweise gelesen (Nicht-JSON übersprungen)");
  eq(round.nextSince, "msg_a2", "since zeigt danach auf die letzte Message-ID");
  ok(String(requests[0]?.url).includes("/ai/json?poll=1"), "poll=1 wird abgefragt");
  ok(String(requests[0]?.url).includes("since=1700000000"), "since wandert in die URL");
  eq(requests[0]?.init?.headers?.Authorization, "Bearer tk_test", "Bearer-Token bei der Abfrage");

  // Keepalive/open-Zeilen tragen keine Nachricht.
  const parsed = parseReplyStream('{"id":"msg_open","event":"open"}\n\n');
  eq(parsed.messages.length, 0, "open-Zeilen ergeben keine Nachricht");
  eq(parsed.nextSince, "msg_open", "die ID wird trotzdem übernommen");

  // Leere Abfrage lässt `since` stehen — sonst gäbe es Endlos-Wiederholungen.
  const emptyRound = await bridge.pollOnce("msg_a2");
  eq(emptyRound.messages.length, 0, "leere Abfrage liefert keine Nachrichten");
  eq(emptyRound.nextSince, "msg_a2", "since bleibt bei leerer Abfrage stehen");
}

// ── 25. Antwort-Brücke: Start, Ablösung und Cleanup ──────────────────────
console.log("\nAntwort-Brücke (Start)");

/**
 * fetch-Ersatz für die Lebensdauer-Tests: die Abfrage bleibt offen, bis der
 * Abort (Poll-Timeout ODER Ablösung durch die nächste Instanz) sie beendet —
 * so ist sichtbar, welche Schleife gerade pollt.
 */
function hangFetch(calls) {
  return (input, init = {}) => {
    const entry = { url: String(input), done: false };
    calls.push(entry);
    return new Promise((resolve) => {
      const finish = () => {
        entry.done = true;
        resolve({ ok: true, status: 200, text: async () => "" });
      };
      if (init.signal?.aborted) finish();
      else init.signal?.addEventListener("abort", finish, { once: true });
    });
  };
}

{
  resetSharedState();
  const runtime = replyRuntime();
  const client = { session: { prompt: async () => {} } };
  const base = (extra = {}) => ({
    env: {
      NTFY_URL: SINK_URL,
      NTFY_TOKEN: "tk_test",
      NTFY_TOPIC: "ai",
      NTFY_REPLY_POLL_SECONDS: "60",
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

  const calls = [];
  const first = startReplyBridge(base({ fetch: hangFetch(calls), controller: new AbortController() }));
  ok(first !== null && typeof first.stop === "function", "Brücke startet und liefert stop()");
  eq(runtime.replyAbort, first.controller, "der Start setzt seinen Controller als aktuellen");
  ok(await until(() => calls.length === 1), "die erste Schleife fragt im Hintergrund ab");

  // Zweite Instanz (Plugin-Reload): sie löst die erste ab.
  const second = startReplyBridge(base({ fetch: hangFetch(calls), controller: new AbortController() }));
  ok(second !== null, "ein zweiter Start ist möglich (kein dauerhaftes „läuft schon“)");
  eq(first.controller.signal.aborted, true, "der zweite Start bricht die vorherige Schleife ab");
  eq(second.controller.signal.aborted, false, "die neue Schleife läuft");
  eq(runtime.replyAbort, second.controller, "aktuelle Schleife ist die der neuesten Instanz");
  ok(await until(() => calls.length === 2), "auch die neue Schleife fragt ab");
  eq(
    calls.filter((call) => !call.done).length,
    1,
    "genau eine Schleife pollt (die alte ist wirklich beendet)"
  );

  // Cleanup der alten Instanz darf die neue Schleife nicht töten.
  first.stop();
  eq(second.controller.signal.aborted, false, "Cleanup der alten Instanz tötet die neue Schleife nicht");
  eq(runtime.replyAbort, second.controller, "der aktuelle Controller bleibt unverändert");

  // stop() der aktuellen Instanz beendet sie; ihr Promise wird erledigt.
  second.stop();
  eq(second.controller.signal.aborted, true, "stop() beendet die aktuelle Schleife");
  ok(
    await until(() => runtime.replyPromise === null),
    "der Schleifen-Promise ist erledigt (Neustart möglich)"
  );

  // Ein späterer setup() startet wieder — kein „läuft schon“-Zustand mehr.
  const third = startReplyBridge(base({ fetch: hangFetch(calls), controller: new AbortController() }));
  ok(third !== null, "nach dem Ende startet ein späterer setup() erneut");
  third.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
}

// ── 26. Antwort-Brücke: since-Zeiger überlebt den Neustart ───────────────
console.log("\nAntwort-Brücke (Neustart)");
{
  resetSharedState();
  const runtime = replyRuntime();
  const urls = [];
  const fetchStub = async (input) => {
    urls.push(decodeURIComponent(String(input)));
    // keepalive-Zeile mit ID: der Zeiger rückt weiter, ohne dass eine
    // Nutzernachricht verarbeitet werden muss.
    return { ok: true, status: 200, text: async () => '{"id":"msg_keep9","event":"keepalive"}\n' };
  };
  const base = {
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai", NTFY_REPLY_POLL_SECONDS: "60" },
    runtime,
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    idleMs: 20,
    log: () => {},
  };

  const first = startReplyBridge({ ...base, since: "1700000000" });
  ok(await until(() => urls.length >= 1), "die erste Schleife fragt ab");
  ok(urls[0].includes("since=1700000000"), "der Startwert wird für die erste Abfrage benutzt");
  ok(await until(() => runtime.replySince === "msg_keep9"), "der since-Zeiger wird fortgeschrieben");
  first.stop();
  await wait();
  urls.length = 0;

  const second = startReplyBridge({ ...base, since: "1700000000" });
  ok(await until(() => urls.length >= 1), "die neue Schleife fragt wieder ab");
  ok(urls[0].includes("since=msg_keep9"), "der fortgeschriebene Zeiger überlebt den Neustart");
  ok(!urls[0].includes("1700000000"), "nicht der Startwert (keine Doppelverarbeitung)");
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai", NTFY_REPLY_POLL_SECONDS: "60" },
    runtime,
    controller: new AbortController(),
    client: { session: { prompt: async () => {} } },
    publish: async () => true,
    fetch: fetchStub,
    errorMs: 20,
    idleMs: 20,
    log: () => {},
  });
  ok(await until(() => requests.length >= 2), "nach einem fetch-Fehler fragt die Schleife erneut ab");
  eq(started.controller.signal.aborted, false, "der Fehler beendet die Schleife nicht");
  started.stop();
  await until(() => runtime.replyPromise === null);
  resetSharedState();
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
  const logPath = "/tmp/opencode/ntfy-reply-bridge-test.log";
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
      NTFY_REPLY_POLL_SECONDS: "60",
      NTFY_REPLY_LOG: logPath,
    },
    runtime,
    // Kein API-Passwort: dieser Test prüft Poll-Log + Prompt (kein Formular).
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
    idleMs: 20,
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
    await until(() => posts.some((p) => String(p.title).startsWith("✅"))),
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

// ── 32. Formular-Antwort: offene native Frage direkt beantworten ──────────
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
  const logPath = "/tmp/opencode/ntfy-form-reply-test.log";
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
  const logPath = "/tmp/opencode/ntfy-form-cancel-test.log";
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
  const logPath = "/tmp/opencode/ntfy-pair-test.log";
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

sink.close();

console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
