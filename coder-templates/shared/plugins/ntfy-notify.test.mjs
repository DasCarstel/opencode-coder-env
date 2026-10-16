// Tests für ntfy-notify.js — läuft ohne OpenCode-Server:
//   node coder-templates/shared/plugins/ntfy-notify.test.mjs
//
// Startet einen lokalen HTTP-Sink, spielt Events ein und prüft, was tatsächlich
// als ntfy-Publish rausgeht (Topic, Titel, Priorität, Body, Click-URL).

import http from "node:http";
import { createNtfyNotifier, isServiceProcess } from "./ntfy-notify.js";

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

const SESSION = "ses_test0001abcdef";
const INFO = {
  id: SESSION,
  title: "Ablauf prüfen und Bericht schreiben",
  subpath: "workspaces/infrastructure/server-management",
  location: { directory: "/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/server-management" },
};

function makeNotifier(sink, extra = {}) {
  return createNtfyNotifier({
    env: {
      NTFY_URL: `http://127.0.0.1:${sink.address().port}`,
      NTFY_TOKEN: "tk_test",
      ...(extra.env ?? {}),
    },
    log: () => {},
    ...extra,
  });
}

const sink = await startSink();

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
  const n = makeNotifier(sink);
  await n.handleEvent({ type: "session.created", properties: { info: INFO } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 0, "session.idle ohne vorherige Arbeit → keine Nachricht");
}

// ── 3. Fertig-Meldung mit TLDR ────────────────────────────────────────────
console.log("\nFertig-Meldung");
received.length = 0;
{
  const n = makeNotifier(sink);
  await n.handleEvent({ type: "session.created", properties: { info: INFO } });
  await n.handleChatMessage({ sessionID: SESSION });
  await n.handleEvent({
    type: "message.part.updated",
    properties: { sessionID: SESSION, part: { type: "text", role: "assistant", text: "Fertig: alle 3 Punkte umgesetzt." } },
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
  const n = makeNotifier(sink);
  received.length = 0;
  await n.handleChatMessage({ sessionID: SESSION });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 1, "idle ohne neue Arbeit → keine zweite Nachricht");
}

// ── 5. Subagent-Sessions werden geschluckt ────────────────────────────────
console.log("\nSubagenten");
{
  const n = makeNotifier(sink);
  received.length = 0;
  await n.handleEvent({
    type: "session.created",
    properties: { info: { id: "ses_child0001", parentID: SESSION, title: "Subagent" } },
  });
  await n.handleEvent({ type: "session.status", properties: { sessionID: "ses_child0001", status: "active" } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: "ses_child0001" } });
  eq(received.length, 0, "Kind-Session meldet nichts");
}

// ── 6. Frage mit Optionen ─────────────────────────────────────────────────
console.log("\nFrage");
{
  const n = makeNotifier(sink);
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: INFO } });
  await n.handleEvent({
    type: "question.asked",
    properties: {
      sessionID: SESSION,
      questions: [{ question: "Wie soll ich fortfahren?", options: [{ label: "A: deployen" }, { label: "B: warten" }] }],
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
  const n = makeNotifier(sink);
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: INFO } });
  await n.handleEvent({
    type: "permission.asked",
    properties: { sessionID: SESSION, permission: { title: "Bash", metadata: { command: "rm -rf /tmp/x" } } },
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
  const n = makeNotifier(sink, { env: { NTFY_URL: `http://127.0.0.1:${sink.address().port}`, NTFY_TOKEN: "" } });
  received.length = 0;
  await n.handleChatMessage({ sessionID: SESSION });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  eq(received.length, 0, "ohne Token wird nichts gesendet");
}

// ── 9. Unbekannter Session-Pfad fällt sauber zurück ───────────────────────
console.log("\nFallback ohne Workspace-Pfad");
{
  const n = makeNotifier(sink);
  received.length = 0;
  await n.handleEvent({ type: "session.created", properties: { info: { id: SESSION, title: "Irgendwas" } } });
  await n.handleEvent({ type: "session.status", properties: { sessionID: SESSION, status: "active" } });
  await n.handleEvent({ type: "session.idle", properties: { sessionID: SESSION } });
  const body = received[0]?.body ?? {};
  eq(body.title, "✅ Fertig — opencode", "Projekt-Fallback");
  eq(body.click, "https://coder.mueller-nas.de/", "URL-Fallback");
}

sink.close();

console.log(`\n${passed} bestanden, ${failed} fehlgeschlagen`);
process.exit(failed === 0 ? 0 : 1);
