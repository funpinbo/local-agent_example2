import { useCallback, useEffect, useRef, useState } from "react";
import { agentApi, follow, type Catalog, type CatalogItem, type McpServer, type Transport } from "./agentApi";
import { useDirectory } from "../util";
import { RunCard, type Turn } from "./RunCard";

const DEFAULT_KEY = "assistant";

const PLACEHOLDER: Record<CatalogItem["input"], string> = {
  question: "사내 문서에 물어보세요. 예) OOM이 났던 원인은?",
  memo: "메모를 붙여 넣거나, 비워 두면 예시 메모로 씁니다",
  request: "요청을 쓰거나 비워 두면 예시 요청으로 실행합니다",
  month: "위에서 고른 달로 실행합니다. 하고 싶은 말이 있으면 적어도 됩니다",
  none: "입력 없이 실행합니다",
};

function readStored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function writeStored(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* 무시 */
  }
}

/**
 * 비서 패널. 지금은 3편에서 만든 설계(그래프)를 골라 돌린다.
 * 4편 1장 Tool Calling 층이 들어오면 "업무 비서" 하나가 기본이 되고, 이 목록은 개발자 보기로 남는다.
 */
export function AssistantPanel({ wide, onToggleWide, onDataChanged }: { wide: boolean; onToggleWide: () => void; onDataChanged: () => void }) {
  const dir = useDirectory();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [offline, setOffline] = useState("");
  const [key, setKey] = useState(() => readStored("agent.key", DEFAULT_KEY));
  const [opts, setOpts] = useState<Record<string, string>>({});
  const [transport, setTransport] = useState<Transport>(() => (readStored("agent.transport", "sse") === "poll" ? "poll" : "sse"));
  const [showSettings, setShowSettings] = useState(false);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [uploadMsg, setUploadMsg] = useState("");
  const [mcp, setMcp] = useState<McpServer[] | null>(null);
  const [mcpBusy, setMcpBusy] = useState(false);
  const stops = useRef(new Map<string, () => void>());
  const bodyRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    agentApi.catalog().then(
      (c) => {
        setCatalog(c);
        setOffline("");
      },
      () => setOffline("비서 서버(8200)에 붙지 못했습니다."),
    );
    agentApi.uploads().then((u) => setFiles(u.files), () => undefined);
    agentApi.mcp().then((m) => setMcp(m.disabled ? [] : m.servers), () => undefined);
  }, []);
  useEffect(load, [load]);
  useEffect(() => () => stops.current.forEach((s) => s()), []);

  const items = catalog?.stages.flatMap((s) => s.items) ?? [];
  const item = items.find((i) => i.key === key) ?? items[0];
  const busy = turns.some((t) => t.status === "running" || t.status === "queued");

  // 고른 설계의 선택지 기본값
  useEffect(() => {
    if (!item) return;
    setOpts(Object.fromEntries(item.options.map((o) => [o.key, o.choices[0][0]])));
  }, [item?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // 아래쪽을 보고 있을 때만 새 내용을 따라 내려간다(위로 올려 읽는 중이면 그대로)
  const stick = useRef(true);
  useEffect(() => {
    const b = bodyRef.current;
    if (b && stick.current) b.scrollTop = b.scrollHeight;
  }, [turns]);

  const patch = useCallback((id: string, f: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? f(t) : t))), []);

  const listen = useCallback(
    (id: string, how: Transport) => {
      stops.current.get(id)?.();
      const stop = follow(id, how, {
        step: (s) => patch(id, (t) => ({ ...t, steps: [...t.steps, s] })),
        status: (v) => {
          patch(id, (t) => ({
            ...t,
            status: v.status,
            result: v.result ?? {},
            interrupt: v.interrupt,
            error: v.error,
            updates: t.updates + 1,
            endedAt: v.status === "done" || v.status === "error" || v.status === "waiting" ? Date.now() : undefined,
          }));
          if (v.status === "done") {
            onDataChanged(); // 상신 등으로 그룹웨어가 바뀌었을 수 있다
            agentApi.mcp().then((m) => setMcp(m.disabled ? [] : m.servers), () => undefined);
          }
        },
      });
      stops.current.set(id, stop);
    },
    [patch, onDataChanged],
  );

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    if (!item || busy) return;
    if (item.input === "question" && !text.trim()) return;
    const prompt = text.trim();
    try {
      const v = await agentApi.run({ key: item.key, text: prompt, ...opts, use_uploads: true, user: dir.me.id });
      setTurns((all) => [
        ...all,
        {
          id: v.id,
          title: item.title,
          prompt: prompt || (item.input === "request" ? item.default_request : ""),
          transport,
          steps: [],
          status: v.status,
          result: {},
          interrupt: null,
          error: "",
          startedAt: Date.now(),
          updates: 0,
        },
      ]);
      setText("");
      setShowSettings(false);
      stick.current = true;
      listen(v.id, transport);
    } catch (err) {
      setOffline((err as Error).message);
    }
  }

  async function resume(id: string, action: "approve" | "reject" | "edit", extra?: { title?: string; args?: unknown; comment?: string }) {
    await agentApi.resume(id, action, extra);
    // SSE 연결도, 폴링도 결정 대기 중에 끊지 않았으므로 그대로 이어서 받는다
    patch(id, (t) => ({ ...t, status: "running", interrupt: null, endedAt: undefined }));
  }

  async function upload(f: File | undefined) {
    if (!f) return;
    setUploadMsg(`${f.name} 올리는 중… (MSDF가 읽고 색인합니다)`);
    try {
      setFiles((await agentApi.upload(f)).files);
      setUploadMsg("");
    } catch (err) {
      setUploadMsg((err as Error).message);
    }
  }

  return (
    <aside className={"assistant" + (wide ? " wide" : "")}>
      <header>
        <div>
          <b>업무 비서</b>{" "}
          <span className={"dot " + (catalog ? "on" : "off")} title={catalog ? "연결됨" : "연결 안 됨"} />
          <div className="muted small">{catalog ? `${catalog.model} · 로컬 · MSDF ${catalog.msdf_mode}` : "연결 확인 중"}</div>
        </div>
        <div className="head-btns">
          <button className="icon" onClick={onToggleWide} title={wide ? "좁게" : "넓게"} aria-label="패널 너비">
            {wide ? "⇥" : "⇤"}
          </button>
          <button className={"icon" + (showSettings ? " on" : "")} onClick={() => setShowSettings(!showSettings)} aria-label="설정">
            ⚙
          </button>
        </div>
      </header>

      {showSettings && catalog && (
        <div className="settings">
          <label>
            실행할 설계
            <select
              value={item?.key}
              onChange={(e) => {
                setKey(e.target.value);
                writeStored("agent.key", e.target.value);
              }}
            >
              {catalog.stages.map((s) => (
                <optgroup key={s.stage} label={s.label}>
                  {s.items.map((i) => (
                    <option key={i.key} value={i.key}>
                      {i.title}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          {item && <p className="muted small">{item.desc}</p>}
          {item?.options.map((o) => (
            <label key={o.key}>
              {o.label}
              <select value={opts[o.key] ?? ""} onChange={(e) => setOpts({ ...opts, [o.key]: e.target.value })}>
                {o.choices.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <label>
            진행 받는 방식
            <select
              value={transport}
              onChange={(e) => {
                const v = e.target.value as Transport;
                setTransport(v);
                writeStored("agent.transport", v);
              }}
            >
              <option value="sse">SSE — 서버가 밀어 준다</option>
              <option value="poll">폴링 — 0.9초마다 묻는다 (3편)</option>
            </select>
          </label>
          <div className="mcp-list">
            <span className="muted">MCP 서버 (chat_ui/agent/servers.yaml)</span>
            {mcp === null && <span className="muted">확인 중…</span>}
            {mcp?.length === 0 && <span className="muted">MCP가 꺼져 있습니다</span>}
            {mcp?.map((v) => (
              <div key={v.name} className="row-srv">
                <span className={"srv " + v.status}>{v.status === "ready" ? "●" : v.status === "needs_env" ? "○" : v.status === "error" ? "✕" : "·"}</span>
                <b>{v.name}</b>
                <span className="muted">
                  {v.status === "ready" && `${v.info.name ?? ""} ${v.info.version ?? ""} · ${v.protocol} · 도구 ${v.used.length}/${v.offered.length}개 사용`}
                  {v.status === "needs_env" && `설정 필요: .env에 ${v.missing_env.join(", ")}`}
                  {v.status === "off" && "아직 연결 안 함 (첫 질문 때 연결)"}
                  {v.status === "connecting" && "연결 중…"}
                  {v.status === "error" && v.error}
                </span>
              </div>
            ))}
            {mcp && mcp.some((v) => v.status === "off" || v.status === "error") && (
              <button
                className="link"
                disabled={mcpBusy}
                onClick={async () => {
                  setMcpBusy(true);
                  try {
                    setMcp((await agentApi.mcpConnect()).servers);
                  } finally {
                    setMcpBusy(false);
                  }
                }}
              >
                {mcpBusy ? "연결 중… (처음에는 npx가 서버를 내려받아 오래 걸린다)" : "지금 연결"}
              </button>
            )}
          </div>
          <div className="uploads">
            <span>올린 문서 (문서 질문에 같이 검색)</span>
            {files.map((f) => (
              <span key={f} className="chip">
                {f}
                <button onClick={async () => setFiles((await agentApi.removeUpload(f)).files)} aria-label="지우기">
                  ✕
                </button>
              </span>
            ))}
            <label className="file-btn">
              + 문서 올리기 (.md .txt .docx)
              <input type="file" accept=".md,.txt,.docx" onChange={(e) => upload(e.target.files?.[0])} hidden />
            </label>
            {uploadMsg && <p className="muted small">{uploadMsg}</p>}
          </div>
        </div>
      )}

      <div
        className="assistant-body"
        ref={bodyRef}
        onScroll={(e) => {
          const b = e.currentTarget;
          stick.current = b.scrollHeight - b.scrollTop - b.clientHeight < 80;
        }}
      >
        {offline && (
          <div className="warn">
            {offline}
            <br />
            <code>uvicorn chat_ui.server:app --port 8200</code>{" "}
            <button className="link" onClick={load}>
              다시 연결
            </button>
          </div>
        )}
        {turns.length === 0 && !offline && (
          <div className="intro">
            <p>
              지금 실행할 설계: <b>{item?.title ?? "…"}</b>
            </p>
            {item?.key === "assistant" && (
              <p className="muted small">
                예) "10월에 OOM 장애가 있었어?", "보안팀이 반려한 결재 사유가 뭐야?", "11월 12일 일정 알려 줘". 과정에서 모델이 실제로 본 프롬프트와 쓴 글자를
                펼쳐 볼 수 있습니다.
              </p>
            )}
            <p className="muted small">⚙에서 3편의 다른 설계(보고서 작성, 결재 상신 등)를 고를 수 있습니다.</p>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} className="turn">
            <div className="bubble me">{t.prompt || <span className="muted">({t.title} 실행)</span>}</div>
            <RunCard turn={t} onResume={(a, extra) => resume(t.id, a, extra)} />
          </div>
        ))}
      </div>

      <form className="assistant-input" onSubmit={send}>
        {item && item.input !== "question" && (
          <div className="input-tag">
            {item.title}
            {item.options.map((o) => ` · ${o.choices.find(([v]) => v === opts[o.key])?.[1] ?? ""}`)}
          </div>
        )}
        <div className="input-row">
          <textarea
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={item ? PLACEHOLDER[item.input] : ""}
            disabled={!catalog}
          />
          <button className="primary" disabled={!catalog || busy || (item?.input === "question" && !text.trim())}>
            {busy ? "실행 중" : item?.input === "question" ? "보내기" : "실행"}
          </button>
        </div>
      </form>
    </aside>
  );
}
