import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const HEALTH_TIMEOUT_MS = 750;
const STARTUP_TIMEOUT_MS = 5000;
const STATE_FETCH_TIMEOUT_MS = 1000;
const DEFAULT_STATE_LIMIT = 6;
const DEFAULT_MAX_STATE_CHARS = 6000;
const SOUL_SECTION_NAME = "soul:accepted-state";

export const name = "soul-dsh";

export function apply(ctx, config = {}) {
  const baseUrl = String(config.baseUrl || process.env.SOUL_API_URL || DEFAULT_BASE_URL).replace(/\/$/, "");
  const projectDir = String(config.projectDir || process.cwd());
  const searchLimit = Number(config.searchLimit || process.env.SOUL_REME_SEARCH_LIMIT || 5);
  const stateLimit = positiveInt(config.stateLimit || process.env.SOUL_DSH_STATE_LIMIT, DEFAULT_STATE_LIMIT);
  const maxStateChars = positiveInt(config.maxStateChars || process.env.SOUL_DSH_MAX_STATE_CHARS, DEFAULT_MAX_STATE_CHARS);
  const autoStart = config.autoStart !== false && process.env.SOUL_DSH_AUTO_START !== "0";
  const injectAcceptedState = config.injectAcceptedState !== false && process.env.SOUL_DSH_INJECT_ACCEPTED_STATE !== "0";
  const apiReady = ensureSoulApi({ baseUrl, projectDir, autoStart }, ctx);

  if (injectAcceptedState) {
    ctx.on("system-prompt/assemble", async (_assembly, assembleContext, next) => {
      const assembly = await next();
      const text = await acceptedStatePrompt(
        { baseUrl, stateLimit, maxStateChars, apiReady, task: latestUserTask(assembleContext?.agent) },
        ctx,
      );
      if (!text) {
        return assembly;
      }
      const sections = assembly.sections.filter((section) => section.name !== SOUL_SECTION_NAME);
      insertAfter(sections, "deployment:persona-prefix", {
        name: SOUL_SECTION_NAME,
        text,
      });
      return {
        ...assembly,
        sections,
      };
    }, { global: true });
  }

  ctx.on("agent/turn-stopping", ({ agent, turn }) => {
    void enqueueTurn({ baseUrl, projectDir, searchLimit, agent, turn, apiReady }, ctx);
  });
}

export default apply;

async function ensureSoulApi({ baseUrl, projectDir, autoStart }, ctx) {
  try {
    if (await isHealthy(baseUrl)) {
      log(ctx, "info", `Soul API is available at ${baseUrl}.`);
      return true;
    }
    if (!autoStart) {
      log(ctx, "warn", `Soul API is not available at ${baseUrl}. Start it with: soul-api --project-dir "${projectDir}"`);
      return false;
    }
    const child = startSoulApi({ baseUrl, projectDir }, ctx);
    if (!child) {
      return false;
    }
    registerProcessCleanup(ctx, child);
    const ready = await waitUntilHealthy(baseUrl, STARTUP_TIMEOUT_MS);
    if (ready) {
      log(ctx, "info", `Soul API started at ${baseUrl}.`);
      return true;
    }
    log(ctx, "warn", `Soul API did not become healthy at ${baseUrl} within ${STARTUP_TIMEOUT_MS}ms.`);
    return false;
  } catch (error) {
    log(ctx, "warn", `Soul API startup check failed: ${compactError(error)}`);
    return false;
  }
}

async function enqueueTurn({ baseUrl, projectDir, searchLimit, agent, turn, apiReady }, ctx) {
  try {
    if (apiReady) {
      await apiReady;
    }
    const session = agent?.session;
    const messages = normalizeMessages(session?.deriveMessages?.() || []);
    const task = latestRole(messages, "user");
    const outcome = latestRole(messages, "assistant");
    if (!task && !outcome) {
      return;
    }
    const sessionId = String(session?.id || agent?.id || "deepseek-harness-session");
    const response = await fetch(`${baseUrl}/evidence/enqueue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        evidence: {
          source: "deepseek-harness",
          task,
          outcome,
          summary: outcome,
          session_id: sessionId,
          turn_id: `turn-${turn}`,
          messages,
        },
        episode: {
          task,
          outcome,
          session_id: sessionId,
          turn_id: `turn-${turn}`,
          events: [{ type: "agent/turn-stopping", turn, project_dir: projectDir }],
          messages,
        },
        reme: {
          search_limit: searchLimit,
        },
      }),
    });
    if (!response.ok) {
      throw new Error(`Soul API ${response.status}: ${await response.text()}`);
    }
  } catch (error) {
    const logger = ctx?.logger;
    if (logger?.warn) {
      logger.warn(`Soul evidence enqueue failed: ${compactError(error)}`);
    }
  }
}

async function acceptedStatePrompt({ baseUrl, stateLimit, maxStateChars, apiReady, task }, ctx) {
  try {
    if (apiReady) {
      await apiReady;
    }
    const url = new URL(`${baseUrl}/state`);
    url.searchParams.set("scope", "project");
    url.searchParams.set("limit", String(stateLimit));
    url.searchParams.set("source", "deepseek-harness");
    if (task) {
      url.searchParams.set("task", task);
    }
    const response = await fetchWithTimeout(url.href, STATE_FETCH_TIMEOUT_MS);
    if (!response.ok) {
      throw new Error(`Soul API ${response.status}: ${await response.text()}`);
    }
    const payload = await response.json();
    const text = unwrapAgentInjection(payload?.injection);
    return truncateText(text, maxStateChars);
  } catch (error) {
    log(ctx, "warn", `Soul accepted state injection skipped: ${compactError(error)}`);
    return "";
  }
}

function startSoulApi({ baseUrl, projectDir }, ctx) {
  try {
    const url = new URL(baseUrl);
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    const host = url.hostname || "127.0.0.1";
    const binPath = resolve(dirname(fileURLToPath(import.meta.url)), "../../bin/soul-api.js");
    const child = spawn(process.execPath, [binPath, "--project-dir", projectDir, "--host", host, "--port", port], {
      cwd: projectDir,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const output = [];
    const remember = (chunk) => {
      const text = String(chunk || "").trim();
      if (!text) {
        return;
      }
      output.push(text);
      if (output.length > 8) {
        output.shift();
      }
    };
    child.stdout?.on("data", remember);
    child.stderr?.on("data", remember);
    child.once("error", (error) => {
      log(ctx, "warn", `Soul API process failed to start: ${compactError(error)}`);
    });
    child.once("exit", (code, signal) => {
      if (code === 0 || signal) {
        return;
      }
      const details = output.length ? ` Output: ${output.join(" ")}` : "";
      log(ctx, "warn", `Soul API process exited early with code ${code}.${details}`);
    });
    return child;
  } catch (error) {
    log(ctx, "warn", `Soul API process launch failed: ${compactError(error)}`);
    return null;
  }
}

async function isHealthy(baseUrl) {
  try {
    const response = await fetchWithTimeout(`${baseUrl}/health`, HEALTH_TIMEOUT_MS);
    if (!response.ok) {
      return false;
    }
    const payload = await response.json().catch(() => ({}));
    return payload?.ok === true;
  } catch {
    return false;
  }
}

async function waitUntilHealthy(baseUrl, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isHealthy(baseUrl)) {
      return true;
    }
    await sleep(250);
  }
  return false;
}

async function fetchWithTimeout(url, timeoutMs, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function insertAfter(sections, afterName, section) {
  const index = sections.findIndex((candidate) => candidate.name === afterName);
  sections.splice(index >= 0 ? index + 1 : 0, 0, section);
}

function positiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function unwrapAgentInjection(injection) {
  const text = String(injection || "").trim();
  const match = text.match(/^\[Soul Current State\]\n([\s\S]*)\n\[\/Soul Current State\]$/);
  return (match ? match[1] : text).trim();
}

function truncateText(text, maxChars) {
  if (!text || text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, Math.max(0, maxChars - 80)).trimEnd()}\n\n[truncated by Soul DSH plugin: maxStateChars=${maxChars}]`;
}

function registerProcessCleanup(ctx, child) {
  const cleanup = () => {
    if (!child.killed) {
      child.kill();
    }
  };
  if (typeof ctx?.effect === "function") {
    ctx.effect(() => cleanup, "soul-dsh: soul-api");
  } else {
    process.once("exit", cleanup);
  }
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function log(ctx, level, message) {
  const logger = ctx?.logger;
  if (typeof logger?.[level] === "function") {
    logger[level](message);
  }
}

function normalizeMessages(messages) {
  const normalized = [];
  for (const message of messages || []) {
    const role = String(message?.role || message?.name || "");
    if (role !== "user" && role !== "assistant") {
      continue;
    }
    const content = messageText(message);
    if (!content) {
      continue;
    }
    normalized.push({ role, content });
  }
  return normalized;
}

function messageText(message) {
  const raw = message?.content ?? message?.text ?? "";
  if (typeof raw === "string") {
    return raw;
  }
  if (Array.isArray(raw)) {
    return raw
      .map((part) => {
        if (typeof part === "string") {
          return part;
        }
        if (part?.type === "text" && typeof part.text === "string") {
          return part.text;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function latestRole(messages, role) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === role) {
      return message.content;
    }
  }
  return "";
}

function latestUserTask(agent) {
  try {
    const messages = normalizeMessages(agent?.session?.deriveMessages?.() || []);
    return latestRole(messages, "user");
  } catch {
    return "";
  }
}

function compactError(error) {
  return String(error instanceof Error ? error.message : error).replace(/\s+/g, " ").slice(0, 500);
}
