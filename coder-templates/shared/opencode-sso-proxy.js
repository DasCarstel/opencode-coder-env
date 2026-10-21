#!/usr/bin/env node
// opencode-sso-proxy.js – transparenter Auth-Proxy für die OpenCode-Web-UI
//
// Zweck: Die Web-UI liefert ihre statischen Dateien ohne Anmeldung aus,
// verlangt aber für jede /api/*-Anfrage HTTP-Basic (User "opencode",
// Passwort aus OpenBao). Ohne Zugangsdaten bleibt die Oberfläche leer.
// Dieser Proxy injiziert das Basic-Auth in jede Anfrage und reicht alles
// an den OpenCode-Server weiter – der Browser braucht keine Zugangsdaten.
//
// Sicherheitsinvariante: Der Proxy bindet AUSSCHLIESSLICH auf 127.0.0.1
// (nach dem Listen wird das geprüft). Ein reiner Loopback-Port ist durch
// Coder/Authentik geschützt, weil er nur über den Coder-Agent als Port-App
// erreichbar ist. Der OpenCode-Server (Port 49374, 0.0.0.0) bleibt
// unverändert geschützt: Er verlangt weiterhin Basic-Auth – der Proxy
// umgeht ihn nur für Clients auf dem Loopback des eigenen Containers.

"use strict";

const http = require("http");
const net = require("net");

const BIND_ADDR = "127.0.0.1";
const PROXY_PORT = parseInt(process.env.OPENCODE_SSO_PROXY_PORT || "49375", 10);
const TARGET_PORT = parseInt(process.env.OPENCODE_SSO_TARGET_PORT || "49374", 10);
const PROXY_USER = process.env.OPENCODE_SSO_PROXY_USER || "opencode";
const PASSWORD = process.env.OPENCODE_SERVER_PASSWORD;

function fail(msg) {
  console.error(`[sso-proxy] FEHLER: ${msg}`);
  process.exit(1);
}

if (!PASSWORD) {
  fail(
    "OPENCODE_SERVER_PASSWORD ist nicht gesetzt – der Proxy braucht das " +
      "OpenCode-Server-Passwort für die Basic-Injektion und startet ohne es nicht."
  );
}
if (!Number.isInteger(PROXY_PORT) || PROXY_PORT < 1 || PROXY_PORT > 65535) {
  fail("OPENCODE_SSO_PROXY_PORT muss ein gültiger Port sein (Standard 49375).");
}
if (!Number.isInteger(TARGET_PORT) || TARGET_PORT < 1 || TARGET_PORT > 65535) {
  fail("OPENCODE_SSO_TARGET_PORT muss ein gültiger Port sein (Standard 49374).");
}
if (PROXY_PORT === TARGET_PORT) {
  fail(
    `Listen-Port (${PROXY_PORT}) und Ziel-Port (${TARGET_PORT}) dürfen nicht ` +
      "identisch sein – der Proxy würde sich selbst weiterleiten."
  );
}

const BASIC_TOKEN =
  "Basic " + Buffer.from(`${PROXY_USER}:${PASSWORD}`, "utf8").toString("base64");

// Header-Objekt für die HTTP-Weiterleitung: Authorization (case-insensitiv)
// entfernen und durch den injizierten Wert ersetzen; alles andere – inklusive
// Host, Origin, Cookie – bleibt unverändert.
function buildHeaders(headers) {
  const out = {};
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === "authorization") continue;
    out[key] = headers[key];
  }
  out["Authorization"] = BASIC_TOKEN;
  return out;
}

// Header-Zeilen aus rawHeaders für das WebSocket-Upgrade neu aufbauen:
// nur Authorization wird ersetzt, Reihenfolge und alle übrigen Paare bleiben.
function rebuildHeaderLines(rawHeaders) {
  const lines = [];
  for (let i = 0; i + 1 < rawHeaders.length; i += 2) {
    if (rawHeaders[i].toLowerCase() === "authorization") continue;
    lines.push(`${rawHeaders[i]}: ${rawHeaders[i + 1]}`);
  }
  lines.push(`Authorization: ${BASIC_TOKEN}`);
  return lines;
}

// Minimaler Service-Worker für GET /sw.js (auch mit Query-String):
// Die OpenCode-Web-UI registriert über /sw.js einen Workbox-Service-Worker;
// ein alter Cache liefert sonst die gespeicherte App-Hülle aus und überdeckt
// die Anmelde-Weiterleitung. Der Proxy beantwortet die Anfrage daher selbst
// mit einem Worker, der sich sofort wieder abmeldet (alter Cache wird
// aufgeräumt), und stellt sicher, dass nichts gespeichert wird (no-store).
const SW_BODY =
  'self.addEventListener("install", () => self.skipWaiting());\n' +
  'self.addEventListener("activate", (event) => event.waitUntil(self.registration.unregister()));\n';

function isServiceWorkerRequest(method, url) {
  if (method !== "GET") return false;
  return url.split("?")[0] === "/sw.js";
}

const server = http.createServer((req, res) => {
  if (isServiceWorkerRequest(req.method, req.url)) {
    res.writeHead(200, {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store",
    });
    res.end(SW_BODY);
    return;
  }
  const upstream = http.request(
    {
      host: BIND_ADDR,
      port: TARGET_PORT,
      method: req.method,
      path: req.url,
      headers: buildHeaders(req.headers),
    },
    (upRes) => {
      // SSE (/api/event): ohne Verzögerung durchreichen, keine Timeouts.
      if (upRes.socket) upRes.socket.setNoDelay(true);
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
      upRes.on("error", () => res.destroy());
    }
  );
  upstream.on("error", (err) => {
    console.error(`[sso-proxy] Upstream-Fehler (${req.method} ${req.url}): ${err.message}`);
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("502 Bad Gateway (OpenCode-Server nicht erreichbar)");
    } else {
      res.destroy();
    }
  });
  req.on("error", () => upstream.destroy());
  res.on("error", () => upstream.destroy());
  req.pipe(upstream);
});

// SSE/Ereignis-Streams: keine Timeouts, kein Buffering.
server.timeout = 0;
server.requestTimeout = 0;
server.keepAliveTimeout = 120000;

server.on("clientError", (err, socket) => {
  console.error(`[sso-proxy] Client-Fehler: ${err.message}`);
  try {
    socket.destroy();
  } catch (_) {
    /* nie abstürzen */
  }
});

// WebSocket-Upgrade (Terminal/PTY): rohen TCP-Tunnel zum Ziel öffnen –
// Request-Zeile + alle Header (nur Authorization ersetzt), dann \r\n\r\n,
// dann vorhandenes head; danach beide Richtungen durchleiten.
server.on("upgrade", (req, socket, head) => {
  const target = net.connect(TARGET_PORT, BIND_ADDR, () => {
    target.write(
      `${req.method} ${req.url} HTTP/1.1\r\n` +
        `${rebuildHeaderLines(req.rawHeaders).join("\r\n")}\r\n\r\n`
    );
    if (head && head.length) target.write(head);
  });
  target.setNoDelay(true);
  socket.pipe(target);
  target.pipe(socket);
  const destroyBoth = (side) => (err) => {
    if (err) console.error(`[sso-proxy] Upgrade-Tunnel-Fehler (${side}): ${err.message}`);
    try {
      socket.destroy();
    } catch (_) {}
    try {
      target.destroy();
    } catch (_) {}
  };
  socket.on("error", destroyBoth("client"));
  target.on("error", destroyBoth("ziel"));
  socket.on("close", () => target.destroy());
  target.on("close", () => socket.destroy());
});

server.on("error", (err) => fail(`Listener-Fehler: ${err.message}`));

server.listen(PROXY_PORT, BIND_ADDR, () => {
  const addr = server.address();
  if (!addr || addr.address !== BIND_ADDR) {
    fail(
      `Listener ist nicht auf ${BIND_ADDR} gebunden (tatsächlich: ` +
        `${addr && addr.address}) – möglicher offener Port, Abbruch.`
    );
  }
  console.log(
    `[sso-proxy] 127.0.0.1:${addr.port} -> 127.0.0.1:${TARGET_PORT} (Basic wird injiziert)`
  );
});
