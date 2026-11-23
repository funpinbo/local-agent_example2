"""챗봇 화면에서 고른 설계 방식(1~4장의 실습 그래프)을 실행하고, 노드·도구 호출·도구 결과를 이벤트로 남긴다.

- 실행은 작업(job) 하나로 백그라운드 스레드에서 돈다. 화면은 이벤트를 주기적으로 받아 그린다.
- LLM은 GPU 하나를 같이 쓰므로 작업은 한 번에 하나씩 돈다(잠금).
- Human-in-the-loop 그래프(4장 04·05)는 interrupt에서 멈추고, 화면의 승인/반려로 이어 간다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from chat_ui import uploads
from common import config, doc_tools, tech_report

ROOT = config.ROOT
MEMOS = {"weekly": "memo_weekly.txt", "trip": "memo_trip.txt", "short": "memo_short.txt"}

_modules: dict[str, Any] = {}
_llm = None
_raw_llm = None
_run_lock = threading.Lock()


def set_llm(llm) -> None:
    """테스트에서 가짜 LLM을 넣을 때 쓴다."""
    global _llm
    _llm = llm


def set_raw_llm(llm) -> None:
    """업무 비서(Tool Calling 층)용 모델. 테스트에서 toolcall.ScriptedRaw를 넣는다."""
    global _raw_llm
    _raw_llm = llm


_mcp_host = None
_mcp_disabled = False


def set_mcp_host(host, disabled: bool = False) -> None:
    """테스트에서 가짜 서버만 띄운 호스트를 넣거나(host), MCP를 끈다(disabled=True)."""
    global _mcp_host, _mcp_disabled
    _mcp_host, _mcp_disabled = host, disabled


def get_mcp_host():
    if _mcp_disabled:
        return None
    if _mcp_host is not None:
        return _mcp_host
    from chat_ui.agent import mcp_host
    return mcp_host.default()


def get_raw_llm():
    global _raw_llm
    if _raw_llm is None:
        from chat_ui.agent.toolcall import RawKanana
        _raw_llm = RawKanana()
    return _raw_llm


def get_llm():
    global _llm
    if _llm is None:
        from common.llm import get_llm as real
        _llm = real()
    return _llm


def load(rel: str):
    if rel not in _modules:
        path = ROOT / rel
        name = "chat_" + rel.replace("/", "_").removesuffix(".py")
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _modules[rel] = mod
    return _modules[rel]


# ---------------------------------------------------------------- 실행할 수 있는 것들

@dataclass
class Item:
    key: str
    stage: str              # chat | s1 | s2 | s3 | s4
    title: str
    file: str | None
    input: str              # memo | request | month | none | question
    desc: str
    build: Callable[[Any, dict], Any] | None = None
    make_input: Callable[[dict], Any] | None = None
    options: list[dict] = field(default_factory=list)
    hitl: bool = False
    default_request: str = ""
    recursion: int = 80


def _req(rel):
    return lambda o: o.get("text") or load(rel).REQUEST


def _memo(o) -> str:
    if o.get("memo_source") == "upload":
        t = uploads.latest_text()
        if t:
            return t
    if o.get("text"):
        return o["text"]
    from infra.msdf.services import agent        # 예시 메모는 MSDF의 memo_text로 받는다
    return agent.fetch("memo_text", {"name": MEMOS[o.get("sample_memo") or "weekly"]})


def _template(o, allow_auto=False):
    t = o.get("template") or "weekly"
    return None if (allow_auto and t == "auto") else ("weekly" if t == "auto" else t)


S1 = "s1_single/"
S2 = "s2_workflow/"
S3 = "s3_multi/"
S4 = "s4_graph/"

TEMPLATE_OPT = {"key": "template", "label": "양식", "choices": [["weekly", "주간 업무 보고"], ["trip", "출장 결과 보고"]]}
MONTH_OPT = {"key": "month", "label": "월", "choices": [["2026-10", "2026년 10월 (결재 6건)"], ["2026-11", "2026년 11월 (결재 없음)"]]}

ITEMS: list[Item] = [
    Item("assistant", "chat", "업무 비서 (Tool Calling)", "chat_ui/agent/assistant.py", "question",
         "4편. 비서 코드 안의 도구(그룹웨어 읽기 10개 + 문서 검색) + MCP 서버 도구(servers.yaml). "
         "모델이 본 프롬프트 원문, 쓴 글자, MCP JSON-RPC 메시지를 그대로 보여 준다.",
         options=[{"key": "stage", "label": "장별 상태 (블로그 재현용)",
                   "choices": [["5", "5장 — MCP 4개 + Skill"], ["3", "3장 — MCP 4개(그룹웨어 서버 포함), Skill 없음"],
                               ["2", "2장 — 비서 코드 도구 + 기성 MCP(time·filesystem·notion)"],
                               ["1", "1장 — 비서 코드 안의 도구만"]]}]),
    Item("chat_qa", "chat", "문서 질문 (챗봇)", None, "question",
         "사내 문서(+올린 문서)를 검색해 근거를 단 답을 준다. 검색 → 답변 → 인용 검사를 코드가 정한 순서로 돈다."),
    # ---- 1. Single Agent
    Item("s1_01", "s1", "기본 Tool Calling Agent", S1 + "01_tool_calling.py", "memo",
         "agent ⇄ tools. 양식 칸 확인 → 저장 도구를 LLM이 부른다.",
         build=lambda llm, o: load(S1 + "01_tool_calling.py").build_graph(llm),
         make_input=lambda o: load(S1 + "01_tool_calling.py").inputs(_memo(o), _template(o)), options=[TEMPLATE_OPT]),
    Item("s1_02", "s1", "ReAct Loop", S1 + "02_react_loop.py", "memo",
         "fill_field로 한 칸씩 채우는 루프. 채운 값은 State의 draft에 쌓인다.",
         build=lambda llm, o: load(S1 + "02_react_loop.py").build_graph(llm),
         make_input=lambda o: load(S1 + "02_react_loop.py").inputs(_memo(o), _template(o)), options=[TEMPLATE_OPT]),
    Item("s1_03", "s1", "Conditional Branch", S1 + "03_conditional_branch.py", "memo",
         "양식을 주면 코드가, 안 주면 LLM이 select_template으로 경로를 정한다.",
         build=lambda llm, o: load(S1 + "03_conditional_branch.py").build_graph(llm),
         make_input=lambda o: load(S1 + "03_conditional_branch.py").inputs(_memo(o), _template(o, allow_auto=True)),
         options=[{"key": "template", "label": "양식", "choices": [["auto", "LLM이 선택"], ["weekly", "주간(코드가 결정)"], ["trip", "출장(코드가 결정)"]]}]),
    Item("s1_04", "s1", "Retry / Fallback / Stop Condition", S1 + "04_retry_fallback_stop.py", "memo",
         "형식 오류·저장 없이 끝냄 → retry 2번 → fallback(구조화 출력). LLM 16번 초과·같은 호출 3번이면 stop.",
         build=lambda llm, o: load(S1 + "04_retry_fallback_stop.py").build_graph(llm),
         make_input=lambda o: load(S1 + "04_retry_fallback_stop.py").inputs(_memo(o), _template(o)), options=[TEMPLATE_OPT]),
    Item("s1_05", "s1", "Reflection / Self-Correction", S1 + "05_reflection.py", "memo",
         "write → review(코드 점검 + 자기 점검) → revise. 수정 2번까지, 남으면 사람에게.",
         build=lambda llm, o: load(S1 + "05_reflection.py").build_graph(llm),
         make_input=lambda o: load(S1 + "05_reflection.py").inputs(_memo(o), _template(o)), options=[TEMPLATE_OPT]),
    # ---- 2. Workflow
    Item("s2_00", "s2", "(먼저) RAG 도구만 붙인 Single Agent", S2 + "00_rag_tool_limit.py", "request",
         "1장 Agent에 doc_search만 붙였다. 검색·저장을 LLM이 정한다.",
         build=lambda llm, o: load(S2 + "00_rag_tool_limit.py").build_graph(llm),
         make_input=lambda o: load(S2 + "00_rag_tool_limit.py").inputs(_req(S2 + "00_rag_tool_limit.py")(o))),
    Item("s2_01", "s2", "Prompt Chaining", S2 + "01_prompt_chaining.py", "request",
         "목차 → (섹션마다) 검색 → 작성 → 관문 → 요약 → 저장. 단계 사이에 코드 관문.",
         build=lambda llm, o: load(S2 + "01_prompt_chaining.py").build_graph(llm),
         make_input=lambda o: load(S2 + "01_prompt_chaining.py").inputs(_req(S2 + "01_prompt_chaining.py")(o)), recursion=120),
    Item("s2_02", "s2", "Routing", S2 + "02_routing.py", "request",
         "요청을 요약/분석/비교로 분류하고 종류별 체인으로 보낸다.",
         build=lambda llm, o: load(S2 + "02_routing.py").build_graph(llm),
         make_input=lambda o: {"request": o.get("text") or load(S2 + "02_routing.py").SAMPLES[1][0]}),
    Item("s2_03", "s2", "Parallelization", S2 + "03_parallelization.py", "none",
         "코드가 정한 4섹션을 동시에 쓰고(reducer로 모음), 검토 3번 다수결.",
         build=lambda llm, o: load(S2 + "03_parallelization.py").build_graph(llm, parallel=o.get("parallel", "1") == "1"),
         make_input=lambda o: {"t0": time.perf_counter()},
         options=[{"key": "parallel", "label": "실행", "choices": [["1", "병렬"], ["0", "순차"]]}]),
    Item("s2_04", "s2", "Orchestrator–Worker", S2 + "04_orchestrator_worker.py", "request",
         "오케스트레이터가 섹션 수를 정하고 Send로 worker를 실행 중에 만든다.",
         build=lambda llm, o: load(S2 + "04_orchestrator_worker.py").build_graph(llm),
         make_input=lambda o: {"request": _req(S2 + "04_orchestrator_worker.py")(o)}),
    Item("s2_05", "s2", "Evaluator–Optimizer", S2 + "05_evaluator_optimizer.py", "none",
         "생성자가 쓰고, 문장과 근거 원문만 받는 평가자가 검사한다. 최대 3회차.",
         build=lambda llm, o: load(S2 + "05_evaluator_optimizer.py").build_graph(llm),
         make_input=lambda o: {}),
    # ---- 3. Multi-Agent
    Item("s3_00", "s3", "(먼저) 도구 12개를 붙인 Single Agent", S3 + "00_many_tools_limit.py", "request",
         "그룹웨어 도구 10개 + 문서 검색 + 저장을 한 Agent에.",
         build=lambda llm, o: load(S3 + "00_many_tools_limit.py").build_graph(llm),
         make_input=lambda o: load(S3 + "00_many_tools_limit.py").inputs(_req(S3 + "00_many_tools_limit.py")(o))),
    Item("s3_01", "s3", "Supervisor / Worker", S3 + "01_supervisor_worker.py", "request",
         "supervisor가 매 차례 메일·일정·결재 담당 Agent 중 누가 일할지 정한다.",
         build=lambda llm, o: load(S3 + "01_supervisor_worker.py").build_graph(llm),
         make_input=lambda o: {"request": _req(S3 + "01_supervisor_worker.py")(o)}),
    Item("s3_02", "s3", "Handoff", S3 + "02_handoff.py", "request",
         "작성 Agent ⇄ 수집 Agent. 넘기는 것도 도구 호출(transfer_to_*)이다.",
         build=lambda llm, o: load(S3 + "02_handoff.py").build_graph(llm),
         make_input=lambda o: load(S3 + "02_handoff.py").inputs(_req(S3 + "02_handoff.py")(o))),
    Item("s3_03", "s3", "Hierarchical Agent", S3 + "03_hierarchical.py", "request",
         "위 관리자 → 수집 팀(팀장+담당 3) / 작성 팀. 팀은 Subgraph로 자기 State를 갖는다. 몇 분 걸린다.",
         build=lambda llm, o: load(S3 + "03_hierarchical.py").build_graph(llm),
         make_input=lambda o: {"request": _req(S3 + "03_hierarchical.py")(o)}),
    Item("s3_04", "s3", "Sequential / Parallel Multi-Agent", S3 + "04_sequential_parallel.py", "none",
         "수집 Agent 셋은 병렬(또는 순차), 섹션 작성은 순차.",
         build=lambda llm, o: load(S3 + "04_sequential_parallel.py").build_graph(llm, parallel=o.get("parallel", "1") == "1"),
         make_input=lambda o: {"t0": time.perf_counter()},
         options=[{"key": "parallel", "label": "수집", "choices": [["1", "병렬"], ["0", "순차"]]}]),
    Item("s3_05", "s3", "Agent 간 상태 및 결과 공유", S3 + "05_shared_state.py", "none",
         "같은 Agent들을 공유 State(대화를 이어 씀)와 결과만 전달 중 하나로 돌린다.",
         build=lambda llm, o: load(S3 + "05_shared_state.py").build_graph(llm, shared=o.get("shared", "1") == "1"),
         make_input=lambda o: {"messages": [], "notes": {}, "seen": [], "agent_inputs": []},
         options=[{"key": "shared", "label": "공유 방식", "choices": [["1", "공유 State"], ["0", "결과만 전달"]]}]),
    # ---- 4. Graph
    Item("s4_01", "s4", "DAG와 Cyclic Graph", S4 + "01_dag_cyclic.py", "month",
         "같은 월간 보고를 DAG(한 번 지나감)와 Cyclic(검토 ⇄ 고쳐 쓰기, 종료 조건 3개)으로.",
         build=lambda llm, o: load(S4 + "01_dag_cyclic.py").build_graph(llm, cyclic=o.get("cyclic", "1") == "1"),
         make_input=lambda o: {"month": o.get("month", "2026-10")},
         options=[MONTH_OPT, {"key": "cyclic", "label": "모양", "choices": [["1", "Cyclic"], ["0", "DAG"]]}]),
    Item("s4_02", "s4", "Dynamic Routing", S4 + "02_dynamic_routing.py", "month",
         "inspect 노드가 모은 기록을 보고 Command(goto=...)로 갈 곳을 실행 중에 정한다.",
         build=lambda llm, o: load(S4 + "02_dynamic_routing.py").build_graph(llm),
         make_input=lambda o: {"month": o.get("month", "2026-11")}, options=[MONTH_OPT]),
    Item("s4_03", "s4", "Planner–Executor · Task Decomposition / Replanning", S4 + "03_planner_executor.py", "month",
         "계획 → 관문 → 실행 → (자료가 0건이면) 재계획.",
         build=lambda llm, o: load(S4 + "03_planner_executor.py").build_graph(llm),
         make_input=lambda o: load(S4 + "03_planner_executor.py").inputs(o.get("month", "2026-11")), options=[MONTH_OPT]),
    Item("s4_04", "s4", "Human-in-the-loop", S4 + "04_human_in_the_loop.py", "month",
         "보고서를 만들고 결재 상신 직전에 멈춘다. 아래에서 승인하면 그룹웨어에 실제로 상신된다.",
         build=lambda llm, o: load(S4 + "04_human_in_the_loop.py").build_graph(llm),
         make_input=lambda o: {"month": o.get("month", "2026-10")}, options=[MONTH_OPT], hitl=True),
    Item("s4_05", "s4", "전체 그래프", S4 + "05_full_graph.py", "month",
         "계획·실행·재계획 → 검토 순환 → 사람 승인 → 상신.",
         build=lambda llm, o: load(S4 + "05_full_graph.py").build_graph(llm),
         make_input=lambda o: {"month": o.get("month", "2026-11")}, options=[MONTH_OPT], hitl=True),
]
BY_KEY = {i.key: i for i in ITEMS}

STAGES = [("chat", "4편 · 업무 비서 / 챗봇"), ("s1", "1. Single Agent"), ("s2", "2. Agent Workflow 패턴"),
          ("s3", "3. Multi-Agent"), ("s4", "4. Graph 기반 Agent")]


def catalog() -> list[dict]:
    out = []
    for stage, label in STAGES:
        items = []
        for i in ITEMS:
            if i.stage != stage:
                continue
            default = ""
            if i.input == "request":
                try:
                    default = (load(i.file).SAMPLES[1][0] if i.key == "s2_02" else load(i.file).REQUEST)
                except Exception:
                    default = ""
            items.append({"key": i.key, "title": i.title, "file": i.file, "input": i.input, "desc": i.desc,
                          "options": i.options, "hitl": i.hitl, "default_request": default})
        out.append({"stage": stage, "label": label, "items": items})
    return out


# ---------------------------------------------------------------- 이벤트로 바꾸기

def _short(v: Any, n: int = 4000) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"…(+{len(v) - n}자)"
    s = json.dumps(v, ensure_ascii=False, default=str)
    return v if len(s) <= n else s[:n] + f"…(+{len(s) - n}자)"


def message_event(node: str, m: BaseMessage) -> dict:
    if isinstance(m, AIMessage):
        return {"type": "ai", "node": node, "agent": m.name, "content": _short(m.content or ""),
                "tool_calls": [{"name": c["name"], "args": c["args"]} for c in m.tool_calls],
                "invalid": [c.get("error") for c in m.invalid_tool_calls],
                "tokens": (m.usage_metadata or {}).get("input_tokens")}
    if isinstance(m, ToolMessage):
        return {"type": "tool", "node": node, "name": m.name, "content": _short(str(m.content))}
    if isinstance(m, SystemMessage):
        return {"type": "system", "node": node, "content": _short(str(m.content), 600)}
    return {"type": "human", "node": node, "content": _short(str(m.content), 2000)}


def update_events(node: str, update: Any) -> list[dict]:
    if isinstance(update, (list, tuple)):
        merged: dict = {"messages": []}
        for it in update:
            u = (it.update if isinstance(it, Command) else it) or {}
            if isinstance(u, dict):
                merged["messages"] += u.get("messages", [])
                for k, v in u.items():
                    if k != "messages":
                        merged.setdefault(k, []).append(v)
        update = merged
    if not isinstance(update, dict):
        return [{"type": "node", "node": node, "state": _short(update)}] if update is not None else []
    evs = [message_event(node, m) for m in update.get("messages", []) or [] if isinstance(m, BaseMessage)]
    rest = {k: v for k, v in update.items() if k not in ("messages", "t0")}
    if rest:
        evs.append({"type": "state", "node": node, "state": json.loads(json.dumps(rest, ensure_ascii=False, default=str))})
    if not evs:
        evs.append({"type": "node", "node": node})
    return evs


RESULT_KEYS = ["llm_calls", "tool_calls", "outcome", "kind", "route", "stop_reason", "passed", "votes", "problems", "unresolved", "draft",
               "sections", "report", "summary", "decisions", "runs", "handoffs", "top_turns", "team_log", "rounds",
               "plans", "executed", "observations", "replans", "writer", "agent_inputs", "timings", "submitted",
               "saved_path", "docx_path", "reviews", "context"]


def result_of(state: dict) -> dict:
    out = {}
    for k in RESULT_KEYS:
        if k in state and state[k] not in (None, [], {}):
            out[k] = json.loads(json.dumps(state[k], ensure_ascii=False, default=str))
    for k in ("saved_path", "docx_path"):
        if k in out:
            p = Path(str(out[k]))
            out[k] = p.name
            out.setdefault("files", []).append(p.name)
    rep = state.get("report")
    if isinstance(rep, dict) and rep.get("saved_path"):
        out.setdefault("files", []).append(Path(rep["saved_path"]).name)
    msgs = state.get("messages") or []
    if msgs and isinstance(msgs[-1], AIMessage) and not msgs[-1].tool_calls and msgs[-1].content:
        out["final_answer"] = _short(msgs[-1].content, 6000)
    return out


# ---------------------------------------------------------------- 문서 질문 챗봇 (코드가 정한 순서)

QA_SYSTEM = ("너는 사내 문서를 근거로 답하는 도우미다. 아래 근거만 써서 3~6문장으로 답한다. "
             "문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호]). 근거에 없으면 모른다고 답한다.")


def run_chat_qa(job: "Job", question: str) -> dict:
    corpus = uploads.corpus() or doc_tools.CORPUS
    job.add({"type": "state", "node": "search", "state": {"corpus": corpus, "query": question}})
    from infra.msdf.services import agent as msdf
    hits = msdf.fetch("doc_search", {"corpus": corpus, "q": question, "k": 5})
    job.add({"type": "tool", "node": "search", "name": "doc_search",
             "content": _short(json.dumps([{k: h[k] for k in ("chunk_id", "source", "heading", "score")} for h in hits],
                                          ensure_ascii=False))})
    if not hits:
        return {"final_answer": "관련 문서를 찾지 못했습니다. 다른 표현으로 물어보거나 문서를 올려 주세요."}
    llm = get_llm()
    msgs = [SystemMessage(QA_SYSTEM), HumanMessage(f"[질문]\n{question}\n\n[근거]\n{tech_report.format_chunks(hits)}")]
    ans = llm.invoke(msgs)
    job.add(message_event("answer", ans))
    problems = tech_report.check_citations(ans.content, {h["chunk_id"] for h in hits})
    job.add({"type": "state", "node": "check", "state": {"problems": problems}})
    return {"final_answer": ans.content, "citations": [{k: h[k] for k in ("chunk_id", "source", "heading", "text")}
                                                       for h in hits if h["chunk_id"] in tech_report.citations(ans.content)],
            "problems": problems}


# ---------------------------------------------------------------- 업무 비서 (4편 1장, Tool Calling 층)

def _user_name(user: str) -> str:
    try:
        import httpx
        for p in httpx.get(f"{config.GROUPWARE_URL}/api/users", timeout=5).json():
            if p["id"] == user:
                return p["name"]
    except Exception:  # noqa: BLE001
        pass
    return user


def run_assistant(job: "Job", question: str) -> dict:
    from chat_ui.agent import assistant, runlog

    log = runlog.default()
    llm = get_raw_llm()
    user = job.opts.get("user") or "jaehwi"
    user_name = _user_name(user)
    logged = log.start(job.id, "assistant", user, question, getattr(llm, "model", "scripted"))
    if not logged and log.error:
        job.add({"type": "state", "node": "runlog", "state": {"경고": log.error}})

    def emit(ev: dict) -> None:
        job.add(ev)
        log.event(job.id, ev)

    def approve(tool, args: dict) -> tuple[str, dict, str]:
        """쓰기 도구 앞에서 멈추고 패널의 승인 카드를 기다린다(job.status = waiting)."""
        job.approval_answer = None
        job.approval_event = threading.Event()
        job.interrupt = {"kind": "tool", "tool": tool.name, "server": tool.source, "args": args,
                         "description": tool.description,
                         "question": f"비서가 '{tool.name}'을(를) 실행하려고 합니다. 실행할까요?"}
        job.add({"type": "interrupt", "node": "human", "content": f"쓰기 도구 {tool.name} — 사람의 승인을 기다립니다."})
        job.set_status("waiting")
        job.approval_event.wait()
        ans = job.approval_answer or {"action": "reject"}
        job.interrupt, job.approval_event = None, None
        job.set_status("running")
        action = ans.get("action")
        if action == "edit" and isinstance(ans.get("args"), dict):
            return "approve", ans["args"], ans.get("comment", "")
        return ("approve" if action == "approve" else "reject"), args, ans.get("comment", "")

    # 장별 상태: 1장 = MCP 없음, 2장 = 기성 MCP만(그룹웨어 서버 없이), 3장 = MCP 전부, 5장 = MCP 전부 + Skill
    stage = str(job.opts.get("stage") or "5")
    use_skills = job.opts.get("skills", "on" if stage == "5" else "off") == "on"
    exclude = {"groupware"} if stage == "2" else set()
    host = get_mcp_host() if stage != "1" else None
    job.add({"type": "state", "node": "setup", "state": {"장별 상태": f"{stage}장", "Skill": use_skills,
                                                         "뺀 MCP 서버": sorted(exclude)}})
    if host is not None:
        host.acting_user = user              # 그룹웨어 MCP에 "누구 대신"으로 실린다
        host.add_listener(emit)              # 이 실행 동안 오간 MCP 메시지를 타임라인과 DB에
    try:
        if host is not None:
            st = host.connect()              # 처음이면 여기서 서버들을 띄우고 tools/list를 받는다
            job.add({"type": "mcp_status", "node": "mcp", "servers": st})
        tl = assistant.tools(host, exclude_servers=exclude)
        r = assistant.run(question, llm, emit, tool_list=tl, host=host, approve=approve, user=user,
                          user_name=user_name, use_skills=use_skills)
    except Exception:
        log.finish(job.id, "error")
        raise
    finally:
        if host is not None:
            host.remove_listener(emit)
    log.finish(job.id, "done", r.answer, r.stop_reason)
    return {"final_answer": r.answer, "stop_reason": r.stop_reason, "llm_calls": r.llm_calls, "tool_calls": r.tool_calls}


# ---------------------------------------------------------------- 작업

@dataclass
class Job:
    id: str
    key: str
    title: str
    opts: dict
    status: str = "queued"          # queued | running | waiting | done | error
    events: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""
    interrupt: Any = None
    started: float = field(default_factory=time.time)
    graph: Any = None
    cfg: dict = field(default_factory=dict)
    approval_event: Any = None              # 업무 비서의 쓰기 도구 승인 대기 (threading.Event)
    approval_answer: Any = None
    _cv: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def add(self, ev: dict) -> None:
        ev["t"] = round(time.time() - self.started, 2)
        with self._cv:
            self.events.append(ev)
            self._cv.notify_all()

    def set_status(self, status: str) -> None:
        """상태를 바꾸고 기다리는 스트림(SSE)을 깨운다. result·interrupt·error는 이보다 먼저 채운다."""
        with self._cv:
            self.status = status
            self._cv.notify_all()

    def wait_change(self, seen: int, status: str, timeout: float) -> bool:
        """이벤트가 seen개보다 늘거나 상태가 status에서 바뀔 때까지 기다린다. 바뀌었으면 True."""
        with self._cv:
            return self._cv.wait_for(lambda: len(self.events) > seen or self.status != status, timeout)

    def status_view(self) -> dict:
        return {"id": self.id, "status": self.status, "next": len(self.events), "result": self.result,
                "error": self.error, "interrupt": self.interrupt}

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "key": self.key, "title": self.title, "status": self.status,
                "events": self.events[since:], "next": len(self.events), "result": self.result,
                "error": self.error, "interrupt": self.interrupt}


JOBS: dict[str, Job] = {}


def _stream(job: Job, inputs: Any) -> None:
    item = BY_KEY[job.key]
    cfg = {**job.cfg, "recursion_limit": item.recursion}
    last: dict = {}
    for mode, step in job.graph.stream(inputs, cfg, stream_mode=["updates", "values"]):
        if mode == "values":
            last = step
            continue
        for node, update in step.items():
            if node == "__interrupt__":
                continue
            for ev in update_events(node, update):
                job.add(ev)
    if item.hitl:
        snap = job.graph.get_state(job.cfg)
        last = snap.values
        if snap.next:
            job.interrupt = json.loads(json.dumps(snap.tasks[0].interrupts[0].value, ensure_ascii=False, default=str))
            job.result = result_of(last)
            job.add({"type": "interrupt", "node": "human", "content": "사람의 결정을 기다립니다."})
            job.set_status("waiting")
            return
    job.result = result_of(last)
    job.set_status("done")


def _msdf_event(job: Job):
    def on_fetch(info: dict) -> None:
        job.add({"type": "msdf", "node": "MSDF", **info})
    return on_fetch


def _work(job: Job, resume: Any = None) -> None:
    from infra.msdf.services import agent as msdf
    with _run_lock:
        job.set_status("running")
        old_corpus = doc_tools.CORPUS
        listener = _msdf_event(job)
        msdf.add_listener(listener)          # 이 작업에서 나간 MSDF 요청을 타임라인에 남긴다
        try:
            if job.opts.get("use_uploads") and uploads.corpus():
                doc_tools.CORPUS = uploads.corpus()
            if job.key == "assistant":
                job.result = run_assistant(job, job.opts.get("text") or "")
                job.set_status("done")
                return
            if job.key == "chat_qa":
                job.result = run_chat_qa(job, job.opts.get("text") or "")
                job.set_status("done")
                return
            item = BY_KEY[job.key]
            if resume is None:
                job.graph = item.build(get_llm(), job.opts)
                job.cfg = {"configurable": {"thread_id": job.id}} if item.hitl else {}
                inputs = item.make_input(job.opts)
                job.add({"type": "start", "node": "START", "content": item.title, "file": item.file})
                _stream(job, inputs)
            else:
                job.add({"type": "human", "node": "human", "content": f"사람의 결정: {json.dumps(resume, ensure_ascii=False)}"})
                job.interrupt = None
                _stream(job, Command(resume=resume))
        except Exception as e:  # 화면에 그대로 보여 준다
            job.error = f"{type(e).__name__}: {e}"
            job.add({"type": "error", "node": "error", "content": traceback.format_exc()[-3000:]})
            job.set_status("error")
        finally:
            doc_tools.CORPUS = old_corpus
            msdf.remove_listener(listener)


def start(key: str, opts: dict) -> Job:
    if key not in BY_KEY:
        raise KeyError(key)
    job = Job(id=uuid.uuid4().hex[:10], key=key, title=BY_KEY[key].title, opts=opts)
    JOBS[job.id] = job
    threading.Thread(target=_work, args=(job,), daemon=True).start()
    return job


def resume(job_id: str, answer: dict) -> Job:
    job = JOBS[job_id]
    if job.status != "waiting":
        raise ValueError("이 작업은 결정을 기다리고 있지 않습니다.")
    if job.approval_event is not None:          # 업무 비서: 루프가 스레드 안에서 기다리고 있다
        job.add({"type": "human", "node": "human", "content": f"사람의 결정: {json.dumps(answer, ensure_ascii=False)}"})
        job.approval_answer = answer
        job.approval_event.set()
        return job
    job.set_status("running")
    threading.Thread(target=_work, args=(job, answer), daemon=True).start()
    return job
