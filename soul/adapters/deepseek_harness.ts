export type SoulHookInput = {
  task?: string;
  scope?: string;
  messages?: Array<{ role: string; content?: string; text?: string }>;
  events?: Array<Record<string, unknown>>;
  outcome?: string;
};

export type SoulHookContext = {
  appendSystemMessage?: (content: string) => void;
  systemMessages?: string[];
  metadata?: Record<string, unknown>;
};

export type SoulHarnessOptions = {
  baseUrl?: string;
  scope?: string;
  stateLimit?: number;
  memoryMode?: "legacy" | "soul_reme" | "off";
  remeSearchLimit?: number;
};

const DEFAULT_BASE_URL = "http://127.0.0.1:8765";

export class DeepSeekHarnessSoulPlugin {
  private readonly baseUrl: string;
  private readonly scope: string;
  private readonly stateLimit: number;
  private readonly memoryMode: "legacy" | "soul_reme" | "off";
  private readonly remeSearchLimit: number;

  constructor(options: SoulHarnessOptions = {}) {
    this.baseUrl = (options.baseUrl ?? process.env.SOUL_API_URL ?? DEFAULT_BASE_URL).replace(/\/$/, "");
    this.scope = options.scope ?? process.env.SOUL_SCOPE ?? "project";
    this.stateLimit = options.stateLimit ?? 10;
    this.memoryMode = options.memoryMode ?? parseMemoryMode(process.env.SOUL_DSH_MEMORY_MODE);
    this.remeSearchLimit = options.remeSearchLimit ?? Number(process.env.SOUL_REME_SEARCH_LIMIT ?? 5);
  }

  async beforeTurn(input: SoulHookInput, ctx: SoulHookContext = {}): Promise<Record<string, unknown>> {
    const task = input.task ?? latestUserText(input.messages) ?? "";
    const url = new URL(`${this.baseUrl}/state`);
    url.searchParams.set("task", task);
    url.searchParams.set("scope", this.scope);
    url.searchParams.set("limit", String(this.stateLimit));
    url.searchParams.set("source", "deepseek-harness");

    const statePayload = await readJson(url);
    const injection = String(statePayload.injection ?? statePayload.context ?? "");
    injectSystemContext(ctx, injection);
    ctx.metadata = { ...(ctx.metadata ?? {}), soulStateVersion: statePayload.state?.version };
    return statePayload;
  }

  async beforeStep(input: SoulHookInput, ctx: SoulHookContext = {}): Promise<Record<string, unknown>> {
    return this.beforeTurn(input, ctx);
  }

  async afterTurn(input: SoulHookInput): Promise<Record<string, unknown>> {
    if (this.memoryMode === "off") {
      return { memoryMode: "off", skipped: true };
    }
    const task = input.task ?? latestUserText(input.messages) ?? "";
    const outcome = input.outcome ?? latestAssistantText(input.messages) ?? "";
    const body = {
      evidence: {
        source: this.memoryMode === "soul_reme" ? "deepseek-harness:reme" : "deepseek-harness",
        task,
        outcome,
        event_count: input.events?.length ?? 0,
      },
      episode: {
        task,
        outcome,
        events: input.events ?? [],
      },
    };
    if (this.memoryMode === "soul_reme") {
      return postJson(`${this.baseUrl}/reme/transition/propose`, {
        ...body,
        reme: {
          search_limit: this.remeSearchLimit,
        },
      });
    }
    return postJson(`${this.baseUrl}/transition/propose`, body);
  }
}

export default function createSoulPlugin(options: SoulHarnessOptions = {}) {
  const plugin = new DeepSeekHarnessSoulPlugin(options);
  return {
    name: "soul-current-state",
    beforeTurn: plugin.beforeTurn.bind(plugin),
    beforeStep: plugin.beforeStep.bind(plugin),
    afterTurn: plugin.afterTurn.bind(plugin),
  };
}

function injectSystemContext(ctx: SoulHookContext, content: string): void {
  if (!content) {
    return;
  }
  if (typeof ctx.appendSystemMessage === "function") {
    ctx.appendSystemMessage(content);
    return;
  }
  ctx.systemMessages = [...(ctx.systemMessages ?? []), content];
}

function latestUserText(messages: SoulHookInput["messages"]): string | undefined {
  return latestRoleText(messages, "user");
}

function latestAssistantText(messages: SoulHookInput["messages"]): string | undefined {
  return latestRoleText(messages, "assistant");
}

function latestRoleText(messages: SoulHookInput["messages"], role: string): string | undefined {
  const message = [...(messages ?? [])]
    .reverse()
    .find((item) => item.role === role);
  return message?.content ?? message?.text;
}

function parseMemoryMode(raw: string | undefined): "legacy" | "soul_reme" | "off" {
  if (raw === "soul_reme" || raw === "off" || raw === "legacy") {
    return raw;
  }
  return "legacy";
}

async function readJson(url: URL): Promise<Record<string, any>> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Soul API ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

async function postJson(url: string, body: Record<string, unknown>): Promise<Record<string, any>> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Soul API ${response.status}: ${await response.text()}`);
  }
  return response.json();
}
