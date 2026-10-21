#!/usr/bin/env node
// Standalone-Test für opencode-sso-proxy.js (keine Abhängigkeiten).
// Startet einen Stub-Upstream und den Proxy als Kindprozess und prüft:
//   1. Basic-Injektion (Stub sieht Basic base64("opencode:testpw"))
//   2. Mitgeschicktes Authorization wird ersetzt
//   3. Upstream-Status 404/500 kommt unverändert durch
//   4. WebSocket-Upgrade-Tunnel (101 + Echo)
//   5. Ohne OPENCODE_SERVER_PASSWORD kein Start
//   6. Listen-Port == Ziel-Port → Start verweigert
//   7. Nicht-Loopback-Adresse ablehnen (wenn eine vorhanden ist)
//   8. GET /sw.js (auch mit Query) → Proxy antwortet selbst, Upstream sieht nichts
//   9. Normaler Pfad (/api/info) geht weiterhin an den Upstream
// Bei Erfolg: "OK", Exit 0. Sonst Fehlerliste, Exit 1.

import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROXY_JS = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "opencode-sso-proxy.js"
);
const PASSWORD = "testpw";
const EXPECTED_AUTH =
  "Basic " + Buffer.from(`opencode:${PASSWORD}`, "utf8").toString("base64");

const failures = [];
const check = (name, cond, detail = "") => {
  if (cond) {
    console.log(`  ✓ ${name}`);
  } else {
    failures.push(`${name}${detail ? " – " + detail : ""}`);
    console.error(`  ✗ ${name}${detail ? " – " + detail : ""}`);
  }
};

// ── Hilfsfunktionen ──────────────────────────────────────────────────────────

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// Stub-Upstream: protokolliert authorization-Header, antwortet mit steuerbaren
// Statuscodes und beantwortet Upgrade mit 101 + Echo.
function startStub() {
  return new Promise((resolve, reject) => {
    const seen = [];
    const srv = http.createServer((req, res) => {
      seen.push(req.headers.authorization || null);
      if (req.url === "/notfound") {
        res.writeHead(404, { "content-type": "text/plain" });
        res.end("nf");
        return;
      }
      if (req.url === "/error") {
        res.writeHead(500, { "content-type": "text/plain" });
        res.end("err");
        return;
      }
      res.writeHead(200, { "content-type": "text/plain" });
      res.end("stub-ok");
    });
    srv.on("upgrade", (req, socket) => {
      seen.push(req.headers.authorization || null);
      socket.setNoDelay(true);
      socket.write(
        "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
      );
      socket.on("data", (d) => socket.write(d)); // Echo für den Tunnel-Test
      socket.on("error", () => socket.destroy());
    });
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => resolve({ srv, port: srv.address().port, seen }));
  });
}

function startProxy({ proxyPort, targetPort, withPassword = true }) {
  const env = {
    ...process.env,
    OPENCODE_SSO_PROXY_PORT: String(proxyPort),
    OPENCODE_SSO_TARGET_PORT: String(targetPort),
    OPENCODE_SSO_PROXY_USER: "opencode",
  };
  if (withPassword) env.OPENCODE_SERVER_PASSWORD = PASSWORD;
  else delete env.OPENCODE_SERVER_PASSWORD;
  const child = spawn(process.execPath, [PROXY_JS], {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let out = "";
  child.stdout.on("data", (d) => (out += d.toString()));
  child.stderr.on("data", (d) => (out += d.toString()));
  return { child, output: () => out };
}

function waitExited(child, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve({ timedOut: true }), timeoutMs);
    child.on("exit", (code) => {
      clearTimeout(t);
      resolve({ code });
    });
  });
}

async function waitConnectable(port, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((res) => {
      const s = net.connect(port, "127.0.0.1");
      s.on("connect", () => {
        s.destroy();
        res(true);
      });
      s.on("error", () => res(false));
    });
    if (ok) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  return false;
}

function httpGet(port, requestPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: "127.0.0.1", port, path: requestPath, method: "GET", headers },
      (res) => {
        let body = "";
        res.on("data", (d) => (body += d.toString()));
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
      }
    );
    req.on("error", reject);
    req.end();
  });
}

// Rotes Upgrade: Roh-Socket an den Proxy, 101 erwarten, dann Payload-Echo.
function upgradeTunnel(proxyPort) {
  return new Promise((resolve, reject) => {
    const sock = net.connect(proxyPort, "127.0.0.1");
    let buf = "";
    let got101 = false;
    const failT = setTimeout(() => {
      sock.destroy();
      reject(new Error("Upgrade-Tunnel-Timeout"));
    }, 5000);
    sock.on("connect", () => {
      sock.write(
        "GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
      );
    });
    sock.on("data", (d) => {
      buf += d.toString("utf8");
      if (!got101 && buf.startsWith("HTTP/1.1 101")) {
        got101 = true;
        sock.write("tunnel-payload");
      }
      if (got101 && buf.includes("tunnel-payload")) {
        clearTimeout(failT);
        sock.destroy();
        resolve(true);
      }
    });
    sock.on("error", (e) => {
      clearTimeout(failT);
      reject(e);
    });
  });
}

// ── Ablauf ───────────────────────────────────────────────────────────────────

const cleanup = [];

try {
  // 1-4 + 7: laufender Proxy gegen Stub-Upstream
  const stub = await startStub();
  cleanup.push(() => stub.srv.close());

  const proxyPort = await freePort();
  const { child, output } = startProxy({ proxyPort, targetPort: stub.port });
  cleanup.push(() => {
    try {
      child.kill("SIGKILL");
    } catch (_) {}
  });

  const reachable = await waitConnectable(proxyPort);
  check("Proxy startet und lauscht", reachable, output().trim());

  // 1) Basic-Injektion
  const r1 = await httpGet(proxyPort, "/x");
  const auth1 = stub.seen[stub.seen.length - 1];
  check(
    "GET /x → 200 mit injiziertem Basic",
    r1.status === 200 && r1.body === "stub-ok" && auth1 === EXPECTED_AUTH,
    `status=${r1.status} auth=${auth1}`
  );
  check(
    "Startzeile auf stdout",
    output().includes(`[sso-proxy] 127.0.0.1:${proxyPort} -> 127.0.0.1:${stub.port}`),
    output().split("\n")[0] || ""
  );

  // 2) Fremdes Authorization wird ersetzt
  const authBefore = stub.seen.length;
  await httpGet(proxyPort, "/y", { Authorization: "Basic evil" });
  const auth2 = stub.seen.slice(authBefore).filter((a) => a !== null);
  check(
    "Client-Authorization wird ersetzt",
    auth2.length === 1 && auth2[0] === EXPECTED_AUTH,
    `gesehen: ${JSON.stringify(auth2)}`
  );

  // 3) Statusdurchreichung
  const r404 = await httpGet(proxyPort, "/notfound");
  const r500 = await httpGet(proxyPort, "/error");
  check(
    "404/500 kommen unverändert durch",
    r404.status === 404 && r500.status === 500,
    `404→${r404.status}, 500→${r500.status}`
  );

  // 4) WebSocket-Upgrade-Tunnel
  try {
    await upgradeTunnel(proxyPort);
    check("Upgrade-Tunnel (101 + Echo)", true);
  } catch (e) {
    check("Upgrade-Tunnel (101 + Echo)", false, e.message);
  }

  // 7) Nur-Loopback-Bindung (wenn es eine Nicht-Loopback-Adresse gibt)
  const nonLoopback = Object.values(os.networkInterfaces())
    .flat()
    .filter((a) => a && a.family === "IPv4" && !a.internal)
    .map((a) => a.address);
  if (nonLoopback.length === 0) {
    console.log("  – Test 7 übersprungen (keine Nicht-Loopback-Adresse vorhanden)");
  } else {
    const refused = await new Promise((res) => {
      const s = net.connect(proxyPort, nonLoopback[0]);
      const done = (v) => {
        s.destroy();
        res(v);
      };
      s.on("connect", () => done(false)); // verbunden = schlecht
      s.on("error", () => done(true)); // abgelehnt = gut
      setTimeout(() => done(true), 1500); // hängend = nicht erreichbar = gut
    });
    check(
      "Nicht von Nicht-Loopback-Adresse erreichbar",
      refused,
      `Adresse: ${nonLoopback[0]}`
    );
  }

  // 8) GET /sw.js wird vom Proxy selbst beantwortet (ohne Upstream)
  const swSeenBefore = stub.seen.length;
  const rswA = await httpGet(proxyPort, "/sw.js");
  const rswB = await httpGet(proxyPort, "/sw.js?v=2");
  const swSeen = stub.seen.slice(swSeenBefore);
  const swOk = (r) =>
    r.status === 200 &&
    (r.headers["content-type"] || "").includes("javascript") &&
    (r.headers["cache-control"] || "") === "no-store" &&
    r.body.includes("unregister");
  check(
    "GET /sw.js → 200, javascript, no-store, abmeldender Body",
    swOk(rswA),
    `status=${rswA.status} ct=${rswA.headers["content-type"]} cc=${rswA.headers["cache-control"]}`
  );
  check(
    "GET /sw.js?v=2 verhält sich genauso",
    swOk(rswB),
    `status=${rswB.status} ct=${rswB.headers["content-type"]} cc=${rswB.headers["cache-control"]}`
  );
  check(
    "Stub sieht keine Anfrage für /sw.js",
    swSeen.length === 0,
    `gesehen: ${JSON.stringify(swSeen)}`
  );

  // 9) Normaler Pfad geht weiterhin an den Upstream
  const beforeNormal = stub.seen.length;
  const rInfo = await httpGet(proxyPort, "/api/info");
  const infoSeen = stub.seen.slice(beforeNormal);
  check(
    "GET /api/info wird weiterhin durchgereicht",
    rInfo.status === 200 && rInfo.body === "stub-ok" && infoSeen.length === 1,
    `status=${rInfo.status} body=${rInfo.body} gesehen=${JSON.stringify(infoSeen)}`
  );

  // 5) Ohne Passwort kein Start
  const port5a = await freePort();
  const port5b = await freePort();
  const p5 = startProxy({ proxyPort: port5a, targetPort: port5b, withPassword: false });
  cleanup.push(() => {
    try {
      p5.child.kill("SIGKILL");
    } catch (_) {}
  });
  const e5 = await waitExited(p5.child);
  check(
    "Ohne OPENCODE_SERVER_PASSWORD kein Start",
    !e5.timedOut && e5.code !== 0 && /OPENCODE_SERVER_PASSWORD/i.test(p5.output()),
    `code=${e5.code ?? "timeout"}`
  );

  // 6) Listen-Port == Ziel-Port → Start verweigert
  const port6 = await freePort();
  const p6 = startProxy({ proxyPort: port6, targetPort: port6 });
  cleanup.push(() => {
    try {
      p6.child.kill("SIGKILL");
    } catch (_) {}
  });
  const e6 = await waitExited(p6.child);
  check(
    "Gleicher Listen-/Ziel-Port wird verweigert",
    !e6.timedOut && e6.code !== 0,
    `code=${e6.code ?? "timeout"}`
  );
} catch (err) {
  failures.push(`Unerwarteter Fehler: ${err && err.stack ? err.stack : err}`);
} finally {
  for (const fn of cleanup) {
    try {
      fn();
    } catch (_) {}
  }
}

if (failures.length > 0) {
  console.error("\nFEHLER:");
  for (const f of failures) console.error(`- ${f}`);
  process.exit(1);
}
console.log("OK");
process.exit(0);
