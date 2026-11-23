import { useEffect, useState } from "react";
import { useDirectory, nameOf } from "../util";
import { agentApi, type Interrupt, type JobStatus, type Step } from "./agentApi";
import { Cited, Timeline } from "./Timeline";

export type Turn = {
  id: string; // job id
  title: string;
  prompt: string;
  transport: "sse" | "poll";
  steps: Step[];
  status: JobStatus;
  result: Record<string, unknown>;
  interrupt: Interrupt | null;
  error: string;
  startedAt: number;
  endedAt?: number;
  updates: number; // 받은 응답 횟수(폴링 비교용)
};

const STATUS_LABEL: Record<JobStatus, string> = {
  queued: "대기",
  running: "실행 중",
  waiting: "결정 대기",
  done: "완료",
  error: "오류",
};

type Section = { heading?: string; title?: string; body?: string; text?: string };

function asSections(r: Record<string, unknown>): Section[] {
  const rep = r.report as unknown;
  const list = (r.sections as unknown) ?? (Array.isArray(rep) ? rep : (rep as { sections?: unknown } | undefined)?.sections);
  return Array.isArray(list) && list.length && typeof list[0] === "object" ? (list as Section[]) : [];
}

function problemsOf(p: unknown): string[] | null {
  if (p === undefined) return null;
  if (Array.isArray(p)) return p.map(String);
  if (p && typeof p === "object") return Object.entries(p).flatMap(([k, a]) => ((a as unknown[]) ?? []).map((x) => `${k}: ${x}`));
  return [];
}

type ResumeFn = (action: "approve" | "reject" | "edit", extra?: { title?: string; args?: unknown; comment?: string }) => Promise<void>;

export function RunCard({ turn, onResume }: { turn: Turn; onResume: ResumeFn }) {
  const live = turn.status === "running" || turn.status === "queued";
  const [open, setOpen] = useState(live);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!live) return;
    setOpen(true);
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [live]);
  useEffect(() => {
    if (turn.status === "done") setOpen(false);
  }, [turn.status]);

  const sec = Math.round(((turn.endedAt ?? (live ? now : Date.now())) - turn.startedAt) / 1000);
  const r = turn.result;
  const files = [...new Set((r.files as string[] | undefined) ?? [])];
  const sections = asSections(r);
  const problems = problemsOf(r.problems);
  const llmCalls = turn.steps.filter((s) => s.type === "ai" || s.type === "llm_raw").length;
  const toolRuns = turn.steps.filter((s) => s.type === "tool").length;
  const toolCalls = turn.steps.reduce((n, s) => n + (s.tool_calls?.length ?? 0), 0) || toolRuns;

  return (
    <div className="run">
      <div className="run-head">
        <span className={`run-status ${turn.status}`}>{STATUS_LABEL[turn.status]}</span>
        <span className="run-title">{turn.title}</span>
        <span className="muted">{sec}초</span>
      </div>

      <button className="run-fold" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} 과정 {turn.steps.length}단계
        {llmCalls > 0 && ` · LLM ${llmCalls}번`}
        {toolCalls > 0 && ` · 도구 호출 ${toolCalls}번`}
        <span className="muted"> · {turn.transport === "sse" ? "SSE" : `폴링 ${turn.updates}번`}</span>
      </button>
      {open && <Timeline steps={turn.steps} />}

      {turn.status === "error" && <div className="warn">{turn.error}</div>}

      {typeof r.final_answer === "string" && (
        <div className="answer">
          <Cited text={r.final_answer} />
        </div>
      )}
      {sections.length > 0 && (
        <div className="report">
          {sections.map((s, i) => (
            <section key={i}>
              <b>{s.heading ?? s.title}</b>
              <p>
                <Cited text={String(s.body ?? s.text ?? "")} />
              </p>
            </section>
          ))}
        </div>
      )}
      {typeof r.summary === "string" && (
        <div className="answer">
          <Cited text={r.summary} />
        </div>
      )}
      {files.length > 0 && (
        <div className="files">
          {files.map((f) => (
            <a key={f} href={agentApi.fileUrl(f)} download>
              📄 {f}
            </a>
          ))}
        </div>
      )}
      {problems && (problems.length ? (
        <details className="problems">
          <summary>근거 검사에서 남은 문제 {problems.length}개</summary>
          <pre>{problems.join("\n")}</pre>
        </details>
      ) : (
        turn.status === "done" && <div className="ok-text">근거 검사 문제 없음</div>
      ))}
      {r.submitted !== undefined && (
        <div className="ok-text">그룹웨어에 상신했습니다 · 결재 #{String((r.submitted as { id?: number })?.id ?? "")}</div>
      )}

      {turn.status === "waiting" && turn.interrupt?.kind === "tool" && <ToolApprovalCard it={turn.interrupt} onResume={onResume} />}
      {turn.status === "waiting" && turn.interrupt && turn.interrupt.kind !== "tool" && <ApprovalCard it={turn.interrupt} onResume={onResume} />}
    </div>
  );
}

/** 업무 비서의 쓰기 도구 승인 (3장). 인자를 고쳐서 실행할 수도 있다. */
function ToolApprovalCard({ it, onResume }: { it: Interrupt; onResume: ResumeFn }) {
  const original = JSON.stringify(it.args ?? {}, null, 2);
  const [text, setText] = useState(original);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (a: "approve" | "reject") => {
    setError("");
    let args: unknown = undefined;
    if (a === "approve" && text !== original) {
      try {
        args = JSON.parse(text);
      } catch {
        return setError("인자가 JSON 형식이 아닙니다");
      }
    }
    setBusy(true);
    try {
      await onResume(args !== undefined ? "edit" : a, { args, comment });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <div className="approve-card">
      <b>✋ {it.question}</b>
      <p className="muted">
        <span className="call mcpc">{it.server?.replace("mcp:", "MCP ")}</span> <code>{it.tool}</code> — {it.description}
      </p>
      <label>
        인자 (고쳐서 실행할 수 있다)
        <textarea className="args" rows={Math.min(12, text.split("\n").length + 1)} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      </label>
      <label>
        의견 (거절할 때 비서에게 전해진다)
        <input value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      <div className="actions">
        <button className="danger" disabled={busy} onClick={() => act("reject")}>
          거절
        </button>
        <button className="primary" disabled={busy} onClick={() => act("approve")}>
          {text !== original ? "고친 인자로 실행" : "실행 승인"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function ApprovalCard({ it, onResume }: { it: Interrupt; onResume: ResumeFn }) {
  const dir = useDirectory();
  const d = it.draft ?? {};
  const [title, setTitle] = useState(d.title ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (a: "approve" | "reject" | "edit") => {
    setBusy(true);
    setError("");
    try {
      await onResume(a, a === "edit" ? { title } : undefined);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const edited = title.trim() !== "" && title !== d.title;
  return (
    <div className="approve-card">
      <b>✋ {it.question ?? "결정해 주세요"}</b>
      <p className="muted">쓰기 동작입니다. 승인하면 그룹웨어에 실제로 결재가 올라갑니다.</p>
      <label>
        제목
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="meta-grid">
        <span>종류</span>
        <span>{d.kind ?? "-"}</span>
        <span>결재선</span>
        <span>{(d.line ?? []).map((u) => nameOf(dir, u)).join(" → ")}</span>
        <span>첨부</span>
        <span>{d.attachment ?? "-"}</span>
      </div>
      {d.body && (
        <details>
          <summary>상신 본문</summary>
          <pre>{d.body}</pre>
        </details>
      )}
      <div className="actions">
        <button className="danger" disabled={busy} onClick={() => act("reject")}>
          반려
        </button>
        <button className="primary" disabled={busy} onClick={() => act(edited ? "edit" : "approve")}>
          {edited ? "제목 고치고 다시 보기" : "승인 → 상신"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
