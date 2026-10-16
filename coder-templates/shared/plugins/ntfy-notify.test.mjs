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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_REPLY_ENABLED: "0" },
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
  const runtime = { sentIds: ["msg_eigen1"], lastSessionID: "", replyBridgeStarted: false };
  const bridge = createReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_TOPIC: "ai" },
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
  const runtime = { sentIds: [], lastSessionID: SESSION, replyBridgeStarted: false };
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

// ── 25. Antwort-Brücke: Start-Guard und Flag ─────────────────────────────
console.log("\nAntwort-Brücke (Start)");
{
  resetSharedState();
  const runtime = replyRuntime();
  const client = { session: { prompt: async () => {} } };

  const off = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_REPLY_ENABLED: "0" },
    runtime,
    client,
    publish: async () => true,
    log: () => {},
  });
  eq(off, null, "NTFY_REPLY_ENABLED=0 startet keine Brücke");
  eq(runtime.replyBridgeStarted, false, "Flag bleibt ausgeschaltet");

  const controller = new AbortController();
  const started = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test", NTFY_REPLY_POLL_SECONDS: "1" },
    runtime,
    controller,
    client,
    publish: async () => true,
    fetch: async () => ({ ok: true, status: 200, text: async () => "" }),
    log: () => {},
  });
  ok(started !== null && typeof started.stop === "function", "Brücke startet und liefert stop()");
  eq(runtime.replyBridgeStarted, true, "Flag verhindert weitere Schleifen");
  const again = startReplyBridge({
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
    runtime,
    client,
    publish: async () => true,
    log: () => {},
  });
  eq(again, null, "ein zweiter Start im selben Prozess wird verhindert");
  started.stop();
  eq(controller.signal.aborted, true, "stop() beendet die Schleife");
  resetSharedState();
}

// ── 26. Antwort-Brücke: setup → Abfrage → Prompt → Ack ───────────────────
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
    env: { NTFY_URL: SINK_URL, NTFY_TOKEN: "tk_test" },
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

sink.close();

console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
