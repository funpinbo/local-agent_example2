// 업무 비서 서버(8200) 호출. 그룹웨어(8100)와 출처가 다르다 → 서버가 CORS로 허용한다.

export const AGENT_URL: string =
  (import.meta.env.VITE_AGENT_URL as string | undefined) ?? `${location.protocol}//${location.hostname}:8200`;

export type Choice = [string, string];
export type ItemOption = { key: string; label: string; choices: Choice[] };
export type CatalogItem = {
  key: string;
  title: string;
  file: string | null;
  input: "question" | "memo" | "request" | "month" | "none";
  desc: string;
  options: ItemOption[];
  hitl: boolean;
  default_request: string;
};
export type Stage = { stage: string; label: string; items: CatalogItem[] };
export type Catalog = { stages: Stage[]; model: string; msdf_mode: string };

export type Step = {
  type: "start" | "ai" | "tool" | "state" | "msdf" | "human" | "interrupt" | "error" | "system" | "node" | "tools" | "llm_raw" | "parsed" | "mcp" | "mcp_status" | "approval" | "skills" | "skill" | "plan" | "step";
  node: string;
  t?: number;
  agent?: string | null;
  content?: string;
  tool_calls?: { name: string; args: unknown }[];
  invalid?: string[];
  tokens?: number | null;
  name?: string;
  state?: unknown;
  file?: string;
  // Tool Calling 층 (4편 1장)
  names?: string[];
  schemas?: unknown[];
  n?: number;
  prompt?: string;
  prompt_delta?: string | null;
  output?: string;
  prompt_tokens?: number | null;
  output_tokens?: number | null;
  ms?: number;
  done_reason?: string;
  first_token?: { token: string; p: number; chosen?: boolean }[];
  request?: unknown;
  calls?: { name: string; args: unknown }[];
  errors?: string[];
  text?: string;
  verdict?: string;
  args?: unknown;
  // MCP (4편 2장)
  sources?: Record<string, string>;
  server?: string;
  dir?: "send" | "recv";
  method?: string | null;
  rpc_id?: string | number | null;
  msg?: Record<string, unknown>;
  bytes?: number;
  servers?: McpServer[];
  // 단계 계획 (서비스 보강)
  plan_steps?: { tool: string; goal: string }[];
  i?: number;
  goal?: string;
  // Skill (5장)
  skills?: { name: string; description: string; allowed_tools: string[]; body_chars: number; path: string }[];
  allowed?: string[];
  tools_before?: number;
  tools_after?: number;
  body_chars?: number;
  hidden?: number;
  // 쓰기 도구 승인 (3장)
  tool?: string;
  decision?: string;
  final_args?: unknown;
  note?: string;
  // msdf
  order?: string;
  sec?: number;
  ok?: boolean;
  mode?: string;
  steps?: string[];
  cached?: string[];
  params?: unknown;
};

export type McpServer = {
  name: string;
  status: "off" | "needs_env" | "connecting" | "ready" | "error";
  error: string;
  missing_env: string[];
  transport: string;
  protocol: string;
  info: { name?: string; version?: string };
  offered: { name: string; description: string; read_only: boolean; used: boolean }[];
  used: string[];
};

export type JobStatus = "queued" | "running" | "waiting" | "done" | "error";
export type Interrupt = {
  kind?: "tool";
  tool?: string;
  server?: string;
  args?: Record<string, unknown>;
  description?: string;
  question?: string;
  draft?: { title?: string; line?: string[]; body?: string; attachment?: string; kind?: string };
};
export type StatusView = {
  id: string;
  status: JobStatus;
  next: number;
  result: Record<string, unknown>;
  error: string;
  interrupt: Interrupt | null;
};
export type JobView = StatusView & { key: string; title: string; events: Step[] };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(AGENT_URL + path, {
    method,
    headers: body && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* 본문 없음 */
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

export const agentApi = {
  catalog: () => call<Catalog>("GET", "/chat/api/catalog"),
  run: (body: Record<string, string | boolean>) => call<JobView>("POST", "/chat/api/run", body),
  poll: (id: string, since: number) => call<JobView>("GET", `/chat/api/run/${id}?since=${since}`),
  resume: (id: string, action: "approve" | "reject" | "edit", extra?: { title?: string; args?: unknown; comment?: string }) =>
    call<JobView>("POST", `/chat/api/run/${id}/resume`, { action, ...extra }),
  mcp: () => call<{ servers: McpServer[]; boot_log: Step[]; disabled: boolean }>("GET", "/chat/api/mcp"),
  mcpConnect: () => call<{ servers: McpServer[] }>("POST", "/chat/api/mcp/connect"),
  uploads: () => call<{ files: string[]; corpus: string | null }>("GET", "/chat/api/uploads"),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<{ files: string[] }>("POST", "/chat/api/uploads", fd);
  },
  removeUpload: (name?: string) =>
    call<{ files: string[] }>("DELETE", `/chat/api/uploads${name ? `?name=${encodeURIComponent(name)}` : ""}`),
  fileUrl: (name: string) => `${AGENT_URL}/chat/files/${encodeURIComponent(name)}`,
};

export type Transport = "sse" | "poll";

/**
 * 작업 진행을 받는다. sse: 서버가 밀어 준다(EventSource). poll: 3편처럼 0.9초마다 묻는다.
 * 돌려준 함수를 부르면 받기를 멈춘다.
 */
export function follow(
  id: string,
  transport: Transport,
  on: { step: (s: Step) => void; status: (v: StatusView) => void; wire?: (kind: "sse" | "poll") => void },
): () => void {
  let stopped = false;
  if (transport === "sse") {
    const es = new EventSource(`${AGENT_URL}/chat/api/run/${id}/events`);
    es.addEventListener("step", (e) => on.step(JSON.parse((e as MessageEvent).data)));
    es.addEventListener("status", (e) => {
      const v: StatusView = JSON.parse((e as MessageEvent).data);
      on.status(v);
      if (v.status === "done" || v.status === "error") es.close(); // 닫지 않으면 브라우저가 다시 붙는다
    });
    on.wire?.("sse");
    return () => es.close();
  }
  let since = 0;
  let timer: ReturnType<typeof setTimeout>;
  const tick = async () => {
    if (stopped) return;
    try {
      const v = await agentApi.poll(id, since);
      on.wire?.("poll");
      v.events.forEach(on.step);
      since = v.next;
      on.status(v);
      if (v.status === "done" || v.status === "error") return;
    } catch {
      /* 잠깐 실패해도 다음에 다시 */
    }
    timer = setTimeout(tick, 900);
  };
  tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
