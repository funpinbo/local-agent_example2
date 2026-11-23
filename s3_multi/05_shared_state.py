"""3단계-05 Agent 간 상태·결과 공유: 공유 State vs 결과만 전달.

    shared:  mail_agent → schedule_agent → approval_agent → writer     (모두 하나의 messages를 이어 씀)
    result:  mail_agent → schedule_agent → approval_agent → writer     (각자 대화는 따로, writer에게는 메모만)

같은 수집 Agent 셋과 작성자로 두 방식을 비교한다.
- shared: 뒤 Agent는 앞 Agent의 도구 호출·결과까지 모두 본다. writer도 전체 대화를 받는다.
- result: 각 Agent의 대화는 그 Agent 안에서 끝나고, 메모(결과)만 다음으로 넘어간다.
writer의 입력 토큰, 근거 id 검사, 걸린 시간을 비교한다.

    python s3_multi/05_shared_state.py [--mode shared|result|both]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from common import gw_tools, monthly_report
from common.agents import react_agent
from common.runlog import RunLog

MONTH = "2026-10"
TITLE = "2026년 10월 월간 업무 보고 (이재휘)"
WORKERS = [("mail_agent", gw_tools.MAIL_TOOLS, "메일 담당", "10월 메일에서 주요 업무, 장애, 요청 사항을 정리한다."),
           ("schedule_agent", gw_tools.EVENT_TOOLS, "일정 담당", "10월 일정에서 회의·출장·교육을 정리한다."),
           ("approval_agent", gw_tools.APPROVAL_TOOLS, "결재 담당", "10월 결재 문서와 결과(승인·반려 사유)를 정리한다.")]
WORKER_SYSTEM = ("너는 {role}이다. 도구를 한 번에 하나씩 써서 그룹웨어 기록을 확인한다. 받은 일에 맞게 확인한 내용을 항목별로 정리해 답한다. "
                 "항목마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다. 기록에 없는 내용은 쓰지 않는다.")
WRITE_ASK = ("보고서 '{title}'를 쓴다. 섹션은 주요 업무, 장애와 대응, 결재 진행, 다음 달 계획. "
             "위 기록만 근거로, 문장마다 근거 id를 [mail:12]처럼 그대로 단다.")


class State(TypedDict, total=False):
    messages: list          # shared 모드에서 모두가 이어 쓰는 대화
    notes: dict             # result 모드에서 넘기는 메모
    seen: list[str]
    agent_inputs: list[dict]
    writer: dict
    saved_path: str


def _seen(msgs) -> set[str]:
    return {r for m in msgs if isinstance(m, ToolMessage) for r in monthly_report.refs_loose(str(m.content))}


def _structured(llm, schema, msgs):
    """구조화 출력과 입력 토큰을 함께 받는다 (가짜 LLM은 include_raw를 무시한다)."""
    out = llm.with_structured_output(schema, include_raw=True).invoke(msgs)
    if isinstance(out, dict) and "parsed" in out:
        raw = out.get("raw")
        return schema.model_validate(out["parsed"]), (getattr(raw, "usage_metadata", None) or {}).get("input_tokens")
    return schema.model_validate(out), None


def build_graph(llm, *, shared: bool):
    agents = {n: react_agent(llm, t, WORKER_SYSTEM.format(role=r)) for n, t, r, _ in WORKERS}

    def make_worker(name: str, task: str):
        def work(state: State):
            task_msg = HumanMessage(f"[{name}에게] {task}\n(대상 기간: {MONTH})")
            if shared:
                start = list(state.get("messages", [])) + [task_msg]          # 앞 Agent의 대화를 그대로 이어 받는다
                out = agents[name].invoke({"messages": start, "steps": 0}, {"recursion_limit": 40})
                new = out["messages"][len(start) - 1:]
                tokens = [(m.usage_metadata or {}).get("input_tokens") for m in new if isinstance(m, AIMessage)]
                return {"messages": out["messages"], "seen": sorted(set(state.get("seen", [])) | _seen(new)),
                        "agent_inputs": state.get("agent_inputs", []) + [{"agent": name, "messages_in": len(start), "input_tokens": tokens}]}
            out = agents[name].invoke({"messages": [task_msg], "steps": 0}, {"recursion_limit": 40})
            last = out["messages"][-1]
            note = last.content if isinstance(last, AIMessage) and not last.tool_calls else ""
            tokens = [(m.usage_metadata or {}).get("input_tokens") for m in out["messages"] if isinstance(m, AIMessage)]
            return {"notes": {**state.get("notes", {}), name: note},
                    "seen": sorted(set(state.get("seen", [])) | _seen(out["messages"])),
                    "agent_inputs": state.get("agent_inputs", []) + [{"agent": name, "messages_in": 1, "input_tokens": tokens}]}
        return work

    def writer(state: State):
        ask = WRITE_ASK.format(title=TITLE)
        if shared:
            msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)] + [HumanMessage(ask)]
        else:
            notes = "\n\n".join(f"[{n}]\n{t}" for n, t in state["notes"].items())
            msgs = [HumanMessage(f"[담당별 메모]\n{notes}\n\n{ask}")]
        s = time.perf_counter()
        rep, tokens = _structured(llm, monthly_report.Report, msgs)
        sections = [x.model_dump() for x in rep.sections]
        known = set(state.get("seen", []))
        problems = [f"{x['heading']}: {p}" for x in sections for p in monthly_report.check_refs(x["body"], known)]
        cited = [r for x in sections for r in monthly_report.refs(x["body"])]
        path = monthly_report.save(rep.title, sections, stem=f"s3_05_{'shared' if shared else 'result'}")
        return {"writer": {"messages_in": len(msgs), "chars_in": sum(len(str(m.content)) for m in msgs),
                           "input_tokens": tokens, "sec": round(time.perf_counter() - s, 2), "cited": len(cited),
                           "unique_cited": len(set(cited)), "problems": problems},
                "saved_path": str(path)}

    g = StateGraph(State)
    names = [n for n, *_ in WORKERS]
    for n, _, _, task in WORKERS:
        g.add_node(n, make_worker(n, task))
    g.add_node("writer", writer)
    chain = [START, *names, "writer", END]
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both", choices=["shared", "result", "both"])
    args = ap.parse_args(argv)
    msdf.connect()
    llm = get_llm()
    for mode in (["shared", "result"] if args.mode == "both" else [args.mode]):
        log = RunLog("s3_05_shared_state", meta={"mode": mode})
        t0 = time.perf_counter()
        state = log.stream(build_graph(llm, shared=(mode == "shared")), {"messages": [], "notes": {}, "seen": [], "agent_inputs": []})
        log.write("end", {"mode": mode, "total_sec": round(time.perf_counter() - t0, 2), "agent_inputs": state["agent_inputs"],
                          "writer": state["writer"], "saved_path": state["saved_path"]})
        print(mode, state["writer"])


if __name__ == "__main__":
    main(sys.argv[1:])
