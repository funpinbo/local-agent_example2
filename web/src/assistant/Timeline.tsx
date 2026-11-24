import type { ReactNode } from "react";
import type { Step } from "./agentApi";

const pretty = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));

/** [mail:12], [03_oom.md#2] 같은 근거 표시를 눈에 띄게 */
export function Cited({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]\n]{1,60}[:#][^\]\n]{1,40}\])/g);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <span key={i} className="cite">
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </>
  );
}

function Fold({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="fold">
      <summary>{summary}</summary>
      {children}
    </details>
  );
}

/** <|eot_id|> 같은 특수 토큰과 <function=…> 호출을 눈에 띄게 */
export function RawText({ text }: { text: string }) {
  const parts = text.split(/(<\|[a-z_]+\|>|<function=[^>]+>|<\/function>)/g);
  return (
    <pre className="raw">
      {parts.map((p, i) =>
        i % 2 ? (
          <span key={i} className={p.startsWith("<|") ? "tok" : "fn"}>
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </pre>
  );
}

function LlmRaw({ s }: { s: Step }) {
  const first = s.first_token ?? [];
  return (
    <>
      <b>LLM #{s.n}</b>{" "}
      <span className="muted">
        입력 {s.prompt_tokens ?? "?"}토큰 → 출력 {s.output_tokens ?? "?"}토큰 · {((s.ms ?? 0) / 1000).toFixed(1)}초
      </span>
      {first.length > 0 && (
        <div className="first-token" title="모델이 첫 토큰으로 고를 수 있었던 후보와 확률. 도구를 부를지 말지가 여기서 갈린다.">
          {first.map((f, i) => (
            <span key={i} className={f.chosen ? "pick" : ""}>
              {f.chosen ? "첫 토큰 " : i === 1 ? "· 후보 " : ""}
              <code>{JSON.stringify(f.token)}</code> {(f.p * 100).toFixed(f.p > 0.999 || f.p < 0.001 ? 2 : 1)}%
            </span>
          ))}
        </div>
      )}
      <div className="raw-label">모델이 쓴 글자</div>
      <RawText text={s.output || "(빈 출력)"} />
      {s.prompt_delta != null ? (
        <Fold summary={<>이번에 프롬프트에 붙은 글자 ({s.prompt_delta.length}자) — 지난 출력 + 도구 결과</>}>
          <RawText text={s.prompt_delta} />
        </Fold>
      ) : null}
      <Fold summary={<>모델이 본 프롬프트 전체 ({(s.prompt ?? "").length.toLocaleString()}자)</>}>
        <RawText text={s.prompt ?? ""} />
      </Fold>
      <Fold summary="Ollama로 보낸 HTTP 요청 (/api/generate, raw)">
        <pre>{pretty(s.request)}</pre>
      </Fold>
    </>
  );
}

function StepBody({ s }: { s: Step }) {
  switch (s.type) {
    case "tools": {
      const bySource: Record<string, number> = {};
      Object.values(s.sources ?? {}).forEach((src) => (bySource[src] = (bySource[src] ?? 0) + 1));
      return (
        <Fold
          summary={
            <>
              <b>도구 {s.names?.length}개</b>{" "}
              {s.hidden ? <span className="muted">(Skill 뒤에 {s.hidden}개 숨김) </span> : null}
              {Object.entries(bySource).map(([src, n]) => (
                <span key={src} className={"call " + (src === "local" ? "" : "mcpc")}>
                  {src === "local" ? "비서 코드" : src === "skill" ? "Skill 로더" : src.replace("mcp:", "MCP ")} {n}
                </span>
              ))}
            </>
          }
        >
          <pre>{pretty(s.schemas)}</pre>
        </Fold>
      );
    }
    case "skills":
      return (
        <Fold summary={<><b>Skill {s.skills?.length}개</b> <span className="muted">— 프롬프트에는 이름·한 줄 설명만</span></>}>
          <pre>
            {(s.skills ?? [])
              .map((k) => `${k.name}: ${k.description}\n  도구 ${k.allowed_tools.join(", ")} · 본문 ${k.body_chars}자 (${k.path})`)
              .join("\n")}
          </pre>
        </Fold>
      );
    case "plan":
      return (
        <span>
          <b>계획</b>{" "}
          {(s.plan_steps ?? []).length === 0 ? (
            <span className="muted">단계 없음 → 평소처럼 진행</span>
          ) : (s.plan_steps ?? []).length === 1 ? (
            <span className="muted">1단계 → 평소처럼 진행</span>
          ) : (
            <ol className="plan-steps">
              {(s.plan_steps ?? []).map((st, i) => (
                <li key={i}>
                  <code>{st.tool}</code> {st.goal}
                </li>
              ))}
            </ol>
          )}
        </span>
      );
    case "step":
      return (
        <span>
          <span className="call stepc">
            {s.i}/{s.n}단계
          </span>{" "}
          <code>{s.tool}</code> <span className="muted">{s.goal}</span>
        </span>
      );
    case "skill":
      return (
        <span>
          <span className="call skillc">📘 {s.name}</span> 불러옴 · 도구 {s.tools_before}개 → <b>{s.tools_after}개</b>{" "}
          <span className="muted">({s.allowed?.join(", ")}) · 절차 {s.body_chars}자</span>
        </span>
      );
    case "approval":
      return (
        <span>
          <b>사람</b>{" "}
          <span className={s.decision === "approve" ? "ok-text" : "warn"}>
            {s.decision === "approve" ? "실행 승인" : "거절"}
          </span>{" "}
          <code>{s.tool}</code>
          {JSON.stringify(s.args) !== JSON.stringify(s.final_args) && s.decision === "approve" && <span className="muted"> (인자를 고침)</span>}
          {s.note && <span className="muted"> · {s.note}</span>}
        </span>
      );
    case "mcp_status":
      return (
        <span>
          <b>MCP 서버</b>{" "}
          {(s.servers ?? []).map((v) => (
            <span key={v.name} className={"srv " + v.status} title={v.error || v.missing_env.join(", ")}>
              {v.status === "ready" ? "●" : v.status === "needs_env" ? "○" : "✕"} {v.name}
              {v.status === "ready" ? ` ${v.used.length}/${v.offered.length}` : v.status === "needs_env" ? " 설정 필요" : ""}
            </span>
          ))}
        </span>
      );
    case "mcp": {
      const send = s.dir === "send";
      const label = s.method ?? (s.msg && "error" in s.msg ? "오류 응답" : "응답");
      return (
        <Fold
          summary={
            <>
              <span className="call mcpc">
                {send ? "→" : "←"} {s.server}
              </span>{" "}
              <code>{label}</code>
              {s.rpc_id != null && <span className="muted"> #{String(s.rpc_id)}</span>}
              <span className="muted"> · {s.bytes?.toLocaleString()}B</span>
            </>
          }
        >
          <pre>{pretty(s.msg)}</pre>
        </Fold>
      );
    }
    case "llm_raw":
      return <LlmRaw s={s} />;
    case "parsed":
      return (
        <>
          <b>파서</b> <span className={s.errors?.length ? "warn" : "muted"}>→ {s.verdict}</span>
          {s.calls && s.calls.length > 0 && <pre>{pretty(s.calls.length === 1 ? s.calls[0] : s.calls)}</pre>}
          {s.errors?.map((e, i) => (
            <div key={i} className="warn">
              {e}
            </div>
          ))}
        </>
      );
    case "start":
      return (
        <>
          <b>시작</b> <span className="muted">{s.content}</span>
        </>
      );
    case "ai": {
      const calls = s.tool_calls ?? [];
      return (
        <>
          <b>LLM</b>
          {s.tokens ? <span className="muted"> · 입력 {s.tokens}토큰</span> : null}
          {s.content ? (
            <div className="step-text">
              <Cited text={s.content} />
            </div>
          ) : null}
          {calls.map((c, i) => (
            <Fold
              key={i}
              summary={
                <>
                  <span className="call">🔧 {c.name}</span> 도구 호출
                </>
              }
            >
              <pre>{pretty(c.args)}</pre>
            </Fold>
          ))}
          {s.invalid?.length ? <div className="warn">형식이 깨진 호출: {s.invalid.join(" / ")}</div> : null}
          {!s.content && !calls.length ? <div className="muted">(빈 응답)</div> : null}
        </>
      );
    }
    case "tool": {
      const text = String(s.content ?? "");
      return (
        <Fold
          summary={
            <>
              <span className={"call " + (s.ok === false ? "bad" : "result")}>↩ {s.name}</span>
              {s.ms != null && <span className="muted"> {s.ms}ms</span>} {text.length > 70 ? text.slice(0, 70) + "…" : text}
            </>
          }
        >
          {s.args !== undefined && s.args !== null && <pre>인자 {pretty(s.args)}</pre>}
          <pre>{text}</pre>
        </Fold>
      );
    }
    case "msdf":
      return (
        <Fold
          summary={
            <>
              <span className="call msdf">MSDF {s.order}</span> {s.sec}초 · {s.ok ? "완료" : "실패"}
              {s.steps?.length ? ` · ${s.steps.join("→")}` : ""}
              {s.cached?.length ? ` · 캐시 ${s.cached.length}` : ""}
            </>
          }
        >
          <pre>{pretty(s.params)}</pre>
        </Fold>
      );
    case "state":
      return (
        <Fold
          summary={
            <>
              <b>State</b> <span className="muted">{Object.keys((s.state as object) ?? {}).join(", ")}</span>
            </>
          }
        >
          <pre>{pretty(s.state)}</pre>
        </Fold>
      );
    case "system":
      return (
        <Fold summary={<b>시스템 프롬프트</b>}>
          <pre>{s.content}</pre>
        </Fold>
      );
    case "interrupt":
      return <b className="wait-text">⏸ {s.content}</b>;
    case "error":
      return (
        <Fold summary={<b className="warn">오류</b>}>
          <pre>{s.content}</pre>
        </Fold>
      );
    case "human":
      return <span>{s.content}</span>;
    default:
      return s.state ? <pre>{pretty(s.state)}</pre> : null;
  }
}

export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="timeline">
      {steps.map((s, i) => (
        <li key={i} className={`step ${s.type}`}>
          <span className="step-t">{s.t?.toFixed(1)}s</span>
          <div className="step-main">
            <span className="step-node">
              {s.agent ? `${s.agent} · ` : ""}
              {s.node}
            </span>
            <StepBody s={s} />
          </div>
        </li>
      ))}
    </ol>
  );
}
