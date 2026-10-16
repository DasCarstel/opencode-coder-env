// ntfy-notify.js — OpenCode → ntfy (selbst gehosteter Notification-Hub)
//
// Meldet an das Topic "ai":
//   session.idle      → Agent fertig (3) + TLDR der letzten Antwort
//   question.asked    → Agent hat eine Frage (4), inkl. Optionen
//   permission.asked  → Freigabe nötig (4)
//   session.error     → Fehler (5)
//
// Aktiv NUR im Hintergrund-Service (`opencode serve …`). TUI-, `run`- und
// Mini-Prozesse sehen dieselben Events und würden alles doppelt melden.
//
// Umgebungsvariablen:
//   NTFY_URL    Basis-URL        (Default https://ntfy.mueller-nas.de)
//   NTFY_TOKEN  Bearer-Token     (ohne Token bleibt das Plugin still)
//   NTFY_TOPIC  Topic            (Default "ai")
//   NTFY_DEBUG=1      Debug-Ausgaben ins Server-Log
//   NTFY_SELFTEST=1   sendet beim Laden genau eine Testnachricht (Diagnose) und
//                     meldet dann keine Events — für den gefahrlosen Funktionstest
//
// Installation: coder-templates/shared/plugins/ntfy-notify.js
//               → ~/.config/opencode/plugins/ (dort automatisch geladen)

const DEFAULT_URL = "https://ntfy.mueller-nas.de";
const DEFAULT_TOPIC = "ai";
const MAX_TITLE = 90;
const MAX_BODY = 500;
const COOLDOWN_MS = 15000;

function debugEnabled(env = process.env) {
  return env.NTFY_DEBUG === "1" || env.NTFY_SELFTEST === "1";
}

function debug(...args) {
  if (debugEnabled()) console.error("[ntfy-notify]", ...args);
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

/** Text einer Message-Part/Info — nur Assistenten-Text, nie der User-Prompt. */
function textFromProperties(properties) {
  const part = properties?.part;
  if (part && typeof part.text === "string" && part.text.trim()) {
    if (part.type && part.type !== "text") return "";
    if (part.role && part.role !== "assistant") return "";
    return part.text.trim();
  }
  const info = properties?.info;
  if (info && typeof info.text === "string" && info.text.trim()) {
    if (info.role && info.role !== "assistant") return "";
    return info.text.trim();
  }
  if (typeof properties?.text === "string" && properties.text.trim()) {
    return properties.text.trim();
  }
  return "";
}

/** Frage + Auswahloptionen als lesbarer Text. */
function questionBody(properties) {
  const raw = properties?.questions ?? properties?.question;
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

function permissionBody(properties) {
  const perm = properties?.permission ?? properties?.info ?? properties ?? {};
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

function errorBody(properties) {
  const err = properties?.error ?? properties?.info?.error ?? properties?.message;
  const text =
    typeof err === "string" ? err : err?.message ?? err?.data?.message ?? err?.name ?? "";
  return oneLine(text, 200);
}

function fallbackBody(properties) {
  try {
    return oneLine(JSON.stringify(properties), 180);
  } catch {
    return "";
  }
}

/**
 * Kern des Plugins — als Fabrik exportiert, damit er ohne OpenCode-Server
 * getestet werden kann (siehe coder-templates/shared/plugins/ntfy-notify.test.mjs).
 */
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

  const meta = new Map(); // sessionID → { title, subpath, directory }
  const children = new Map(); // Kind-Session → Eltern-Session
  const working = new Set(); // Sessions, die gerade arbeiten
  const lastText = new Map(); // letzter Assistenten-Text je Session
  const lastSent = new Map(); // Dedup: "sessionID:art" → Zeitstempel

  function metaFor(sessionID) {
    return meta.get(sessionID) ?? {};
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

  function prune() {
    const limit = 200;
    for (const map of [meta, children, lastText, lastSent]) {
      if (map.size <= limit) continue;
      const excess = map.size - limit;
      let i = 0;
      for (const key of map.keys()) {
        map.delete(key);
        if (++i >= excess) break;
      }
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
    if (t - (lastSent.get(key) ?? 0) < cooldownMs) return false;
    lastSent.set(key, t);
    prune();
    return send(payload);
  }

  function withHeader(sessionID, body) {
    const title = titleFor(sessionID);
    return body ? `${title}\n\n${body}` : title;
  }

  async function handleChatMessage(payload) {
    const sessionID =
      typeof payload?.sessionID === "string"
        ? payload.sessionID
        : payload?.event?.properties?.sessionID;
    if (sessionID && !children.has(sessionID)) working.add(sessionID);
  }

  async function handleEvent(event) {
    const type = event?.type;
    if (typeof type !== "string") return;
    const properties = event?.properties ?? {};
    const sessionID =
      typeof properties.sessionID === "string" ? properties.sessionID : undefined;

    const info = properties.info ?? properties.message?.info;
    if (info?.id) {
      if (info.parentID) children.set(info.id, info.parentID);
      const entry = meta.get(info.id) ?? {};
      if (typeof info.title === "string" && info.title.trim()) entry.title = info.title.trim();
      if (typeof info.subpath === "string" && info.subpath) entry.subpath = info.subpath;
      const dir = info.location?.directory ?? info.location?.path ?? info.directory;
      if (typeof dir === "string" && dir) entry.directory = dir;
      meta.set(info.id, entry);
    }
    // Subagent-Sessions (parentID gesetzt) melden nichts — die Eltern-Session tut es.
    if (sessionID && children.has(sessionID)) return;

    const text = textFromProperties(properties);
    if (sessionID && text) lastText.set(sessionID, text);

    switch (type) {
      case "session.status": {
        const kind =
          typeof properties.status === "string" ? properties.status : properties.status?.type;
        if (sessionID && typeof kind === "string" && kind.toLowerCase() !== "idle") {
          working.add(sessionID);
        }
        return;
      }
      case "tool.execute.before":
      case "tool.execute.after":
      case "session.compacted":
        if (sessionID) working.add(sessionID);
        return;
      case "session.idle": {
        // Nur melden, wenn die Session wirklich gearbeitet hat — sonst würde
        // jede frisch angelegte/leere Session eine Meldung auslösen.
        if (!sessionID || !working.has(sessionID)) return;
        working.delete(sessionID);
        const summary = clampText(lastText.get(sessionID) ?? "", 260);
        lastText.delete(sessionID);
        await sendOnce(sessionID, "idle", {
          title: `✅ Fertig — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, summary),
          priority: 3,
          tags: ["white_check_mark"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "question.asked": {
        working.add(sessionID);
        await sendOnce(sessionID, "question", {
          title: `❓ Frage — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, questionBody(properties) || fallbackBody(properties)),
          priority: 4,
          tags: ["question"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "permission.asked": {
        working.add(sessionID);
        await sendOnce(sessionID, "permission", {
          title: `🔐 Freigabe — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, permissionBody(properties) || fallbackBody(properties)),
          priority: 4,
          tags: ["lock"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "session.error": {
        await sendOnce(sessionID, "error", {
          title: `⚠️ Fehler — ${projectFor(sessionID)}`,
          message: withHeader(sessionID, errorBody(properties) || fallbackBody(properties)),
          priority: 5,
          tags: ["warning"],
          click: linkFor(sessionID),
        });
        return;
      }
      case "session.deleted": {
        if (sessionID) {
          meta.delete(sessionID);
          working.delete(sessionID);
          lastText.delete(sessionID);
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
      message: `Selbsttest des OpenCode-Plugins.\nURL: ${url}\nTopic: ${topic}`,
      priority: 2,
      tags: ["test_tube"],
      click: String(env.NTFY_FALLBACK_URL ?? "https://coder.mueller-nas.de/"),
    });
  }

  return {
    handleEvent,
    handleChatMessage,
    send,
    sendOnce,
    selfTest,
    projectFor,
    linkFor,
    _state: { meta, children, working, lastText, lastSent },
  };
}

export const NtfyNotifyPlugin = async (context) => {
  const directory = typeof context?.directory === "string" ? context.directory : "";
  const selftest = process.env.NTFY_SELFTEST === "1";

  // Nur der Hintergrund-Service meldet; im Selbsttest auch ein Zweit-Server.
  if (!selftest && !isServiceProcess()) return {};

  if (!process.env.NTFY_TOKEN && !selftest) {
    console.error(
      "[ntfy-notify] NTFY_TOKEN fehlt (OpenBao secret/data/mcp/notify, Feld opencode_token) " +
        "– Benachrichtigungen deaktiviert"
    );
    return {};
  }

  const notifier = createNtfyNotifier({ directory });

  if (selftest) {
    console.error("[ntfy-notify] Selbsttest wird gesendet …");
    await notifier.selfTest();
    return {};
  }

  return {
    "chat.message": async (payload) => {
      try {
        await notifier.handleChatMessage(payload);
      } catch (error) {
        console.error("[ntfy-notify] chat.message-Fehler:", error?.message ?? error);
      }
    },
    event: async ({ event }) => {
      try {
        await notifier.handleEvent(event);
      } catch (error) {
        console.error("[ntfy-notify] Event-Fehler:", error?.message ?? error);
      }
    },
  };
};

export default {
  id: "das.ntfy-notify",
  server: NtfyNotifyPlugin,
  setup() {},
};
