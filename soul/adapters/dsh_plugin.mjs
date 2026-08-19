const DEFAULT_BASE_URL = "http://127.0.0.1:8765";

export const name = "soul-dsh";

export function apply(ctx, config = {}) {
  const baseUrl = String(config.baseUrl || process.env.SOUL_API_URL || DEFAULT_BASE_URL).replace(/\/$/, "");
  const projectDir = String(config.projectDir || process.cwd());
  const searchLimit = Number(config.searchLimit || process.env.SOUL_REME_SEARCH_LIMIT || 5);

  ctx.on("agent/turn-stopping", ({ agent, turn }) => {
    void enqueueTurn({ baseUrl, projectDir, searchLimit, agent, turn }, ctx);
  });
}

export default apply;

async function enqueueTurn({ baseUrl, projectDir, searchLimit, agent, turn }, ctx) {
  try {
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

function compactError(error) {
  return String(error instanceof Error ? error.message : error).replace(/\s+/g, " ").slice(0, 500);
}
