// ntfy-notify.js — OpenCode V2 → ntfy (selbst gehosteter Notification-Hub)
//
// Meldet an das Topic "ai":
//   session.idle      → Agent fertig (3) + TLDR der letzten Antwort
//   question.asked    → Agent hat eine Frage (4), inkl. Optionen
//   permission.asked  → Freigabe nötig (4)
//   session.error     → Fehler (5)
//
// V2-Plugin (Plugin-Definition mit id + setup(ctx)); V1-Plugins laufen in
// OpenCode 2.x nicht mehr. Events kommen aus ctx.event.subscribe(), das
// "arbeitet gerade"-Signal aus dem ctx.session.hook("prompt")-Hook.
//
// Aktiv NUR im Hintergrund-Service (`opencode serve …`): TUI-, `run`- und
// Web-Prozesse laden dasselbe Plugin, würden aber dieselben Events doppelt
// melden. Das Modul ist ein Singleton je Prozess — Zustand (working/Dedup)
// wird über alle Plugin-Instanzen (eine je Location) geteilt, damit ein
// Event, das mehrere Instanzen sehen, nur eine Nachricht erzeugt.
//
// Umgebungsvariablen:
//   NTFY_URL    Basis-URL        (Default https://ntfy.mueller-nas.de)
//   NTFY_TOKEN  Bearer-Token     (ohne Token bleibt das Plugin still)
//   NTFY_TOPIC  Topic            (Default "ai")
//   NTFY_DEBUG=1      Debug-Ausgaben ins OpenCode-Log
//   NTFY_SELFTEST=1   sendet beim Laden genau eine Testnachricht (Diagnose),
//                     unabhängig vom Prozess-Check
//
// Installation: coder-templates/shared/plugins/ntfy-notify.js
//               → ~/.config/opencode/plugins/ (dort automatisch geladen)

const DEFAULT_URL = "https://ntfy.mueller-nas.de";
const DEFAULT_TOPIC = "ai";
const MAX_TITLE = 90;
const MAX_BODY = 500;
const COOLDOWN_MS = 15000;
const PLUGIN_ID = "das.ntfy-notify";

// ── Hilfsfunktionen ────────────────────────────────────────────────────────

function debug(...args) {
  if (process.env.NTFY_DEBUG === "1" || process.env.NTFY_SELFTEST === "1") {
    console.error("[ntfy-notify]", ...args);
  }
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

/** "/home/<owner>/opencode-coder-env/…" → owner */
function ownerFromDirectory(dir) {
  const m = /^\/home\/([^/]+)\//.exec(String(dir ?? ""));
  return m ? m[1] : "";
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

function sessionIDOf(event, info) {
  const direct = pick(event, "sessionID");
  if (typeof direct === "string" && direct) return direct;
  if (info && typeof info.id === "string" && info.id) return info.id;
  return undefined;
}

/** Text einer Message-Part — nur Assistenten-Text, nie der User-Prompt. */
function textFromEvent(event) {
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

/** Frage + Auswahloptionen als lesbarer Text. */
function questionBody(event) {
  const raw = pick(event, "questions") ?? pick(event, "question");
  const list = Array.isArray(raw) ? raw : raw ? [raw] : [];
  const lines = [];
  for (const q of list.slice(0, 3)) {
    const text = oneLine(q?.question ?? q?.text ?? q?.title ?? q?.header ?? q?.label ?? "", 160);
    if (!text) continue;
    const options = Array.isArray(q?.options)
      ? q.options
          .map((o) => oneLine(o?.label ?? o?.title ?? o?.value ?? o ?? "", 28))
          .filter(Boolean)
      : [];
    lines.push(options.length ? `${text}\n→ ${options.join(" | ")}` : text);
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
  if (what && where) return `${what}: ${where}`;
  return what || where;
}

function errorBody(event) {
  const err = pick(event, "error") ?? pick(event, "message");
  const text =
    typeof err === "string" ? err : err?.message ?? err?.data?.message ?? err?.name ?? "";
  return oneLine(text, 200);
}

function fallbackBody(event) {
  try {
    return oneLine(JSON.stringify(pick(event, "properties") ?? event), 180);
  } catch {
    return "";
  }
}

// ── Gemeinsamer Zustand (ein Zustand je Prozess, mehrere Instanzen) ────────
//
// OpenCode lädt das Plugin einmal je Location (im Infrastructure-Container
// z. B. 6×: /, /root und je Workspace-Ordner). Jede Instanz abonniert den
// öffentlichen Event-Stream und sieht daher dieselben Events. Der Zustand
// hängt deshalb am Prozess (globalThis) und nicht am Modul: so erzeugt ein
// Event genau eine Nachricht, egal wie viele Instanzen es beobachten.

const STATE_KEY = Symbol.for("das.ntfy-notify.state");

function newState() {
  return {
    meta: new Map(), // sessionID → { title, subpath, directory, parentID }
    children: new Map(), // Kind-Session → Eltern-Session
    working: new Set(), // Sessions, die gerade arbeiten
    lastText: new Map(), // letzter Assistenten-Text je Session
    lastSent: new Map(), // Dedup: "sessionID:art" → Zeitstempel
  };
}

function sharedState() {
  if (!globalThis[STATE_KEY]) globalThis[STATE_KEY] = newState();
  return globalThis[STATE_KEY];
}

const SHARED = sharedState();

/** Nur für Tests: geteilten Zustand zurücksetzen. */
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
  const topic = String(options.topic ?? env.NTFY_TOPIC ?? DEFAULT_TOPIC);
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const now = options.now ?? (() => Date.now());
  const logger = options.log ?? debug;
  const cooldownMs = Number(options.cooldownMs ?? COOLDOWN_MS);
  const contextDirectory = options.directory ?? "";
  const client = options.client;
  const state = options.state ?? SHARED;

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

  function linkFor(sessionID) {
    const parsed = parseSubpath(metaFor(sessionID).subpath);
    const owner =
      ownerFromDirectory(metaFor(sessionID).directory) || ownerFromDirectory(contextDirectory);
    if (parsed.workspace && owner) {
      // Coder-Wildcard-App-URL, z. B.
      // herdr--infrastructure--carstenmueller2002-coder.mueller-nas.de
      return `https://herdr--${parsed.workspace}--${owner}-coder.mueller-nas.de`;
    }
    return String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/");
  }

  function absorb(info) {
    if (!info || typeof info !== "object" || typeof info.id !== "string" || !info.id) return;
    if (info.parentID) state.children.set(info.id, info.parentID);
    const entry = state.meta.get(info.id) ?? {};
    if (typeof info.title === "string" && info.title.trim()) entry.title = info.title.trim();
    if (typeof info.subpath === "string" && info.subpath) entry.subpath = info.subpath;
    const dir = info.location?.directory ?? info.location?.path ?? info.directory;
    if (typeof dir === "string" && dir) entry.directory = dir;
    if (info.parentID) entry.parentID = info.parentID;
    state.meta.set(info.id, entry);
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

  async function send(payload) {
    if (!url || !token) {
      logger("kein NTFY_TOKEN/NTFY_URL gesetzt – Nachricht verworfen");
      return false;
    }
    const body = {
      topic,
      message: clampText(payload.message, MAX_BODY) || "…",
      title: oneLine(payload.title, MAX_TITLE),
      priority: payload.priority ?? 3,
      tags: payload.tags ?? ["speech_balloon"],
    };
    if (payload.click) {
      body.click = payload.click;
      body.actions = [{ action: "view", label: "Session öffnen", url: payload.click }];
    }
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
      if (!res?.ok) logger(`Publish fehlgeschlagen: HTTP ${res?.status ?? "?"}`);
      return Boolean(res?.ok);
    } catch (error) {
      logger("Publish-Fehler:", error?.message ?? String(error));
      return false;
    }
  }

  async function sendOnce(sessionID, kind, payload) {
    const key = `${sessionID ?? "?"}:${kind}`;
    const t = now();
    if (t - (state.lastSent.get(key) ?? 0) < cooldownMs) return false;
    state.lastSent.set(key, t);
    prune();
    return send(payload);
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

    // Subagent-Sessions (parentID gesetzt) melden nichts — die Eltern-Session tut es.
    if (sessionID && state.children.has(sessionID)) return;

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
      case "message.part.updated":
      case "message.updated":
      case "tool.execute.before":
      case "tool.execute.after":
      case "session.compacted":
        if (sessionID) state.working.add(sessionID);
        return;
      case "session.idle": {
        // Nur melden, wenn die Session wirklich gearbeitet hat — sonst würde
        // jede frisch angelegte/leere Session eine Meldung auslösen.
        if (!sessionID || !state.working.has(sessionID)) return;
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
        return;
      }
      case "question.asked":
      case "question.requested": {
        if (!sessionID) return;
        markWorking(sessionID);
        await ensureMeta(sessionID);
        await sendOnce(sessionID, "question", {
          title: `❓ Frage — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, questionBody(event) || fallbackBody(event)),
          priority: 4,
          tags: ["question"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "permission.asked":
      case "permission.requested": {
        if (!sessionID) return;
        markWorking(sessionID);
        await ensureMeta(sessionID);
        await sendOnce(sessionID, "permission", {
          title: `🔐 Freigabe — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, permissionBody(event) || fallbackBody(event)),
          priority: 4,
          tags: ["lock"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "session.error": {
        if (!sessionID) return;
        await ensureMeta(sessionID);
        await sendOnce(sessionID, "error", {
          title: `⚠️ Fehler — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, errorBody(event) || fallbackBody(event)),
          priority: 5,
          tags: ["warning"],
          click: linkFor(sessionID),
        });
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
      click: String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/"),
    });
  }

  return {
    handleEvent,
    markWorking,
    send,
    sendOnce,
    selfTest,
    absorb,
    ensureMeta,
    projectFor,
    linkFor,
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
  const argv = options.argv ?? process.argv.slice(2);
  const selftest = env.NTFY_SELFTEST === "1";

  return {
    id: PLUGIN_ID,
    async setup(ctx) {
      // Nur der Hintergrund-Service meldet; im Selbsttest auch ein Test-Server.
      if (!selftest && !isServiceProcess(argv)) return;

      const notifier = createNtfyNotifier({
        env,
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

      if (!env.NTFY_TOKEN) {
        console.error(
          `[ntfy-notify] NTFY_TOKEN fehlt (OpenBao secret/data/mcp/notify, Feld opencode_token) ` +
            `– Benachrichtigungen deaktiviert`
        );
        return;
      }

      console.log(
        `[ntfy-notify] aktiv (topic ${env.NTFY_TOPIC ?? DEFAULT_TOPIC}, location ${
          ctx?.location?.directory ?? "?"
        })`
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
      void (async () => {
        try {
          for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
            try {
              await notifier.handleEvent(event);
            } catch (error) {
              console.error("[ntfy-notify] Event-Fehler:", error?.message ?? error);
            }
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            console.error("[ntfy-notify] Event-Stream beendet:", error?.message ?? error);
          }
        }
      })();

      return () => controller.abort();
    },
  };
}

export default createPluginRuntime();
