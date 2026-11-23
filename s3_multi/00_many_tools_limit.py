"""3단계-00 먼저 도구 10여 개를 한 Agent에 붙여 보기.

    START → agent ⇄ tools → END        도구 12개: 그룹웨어 10 + 사내 문서 검색 + 저장

같은 요청을 여러 번 돌려 기록한다.
- 프롬프트 비대: 도구 설명만으로 첫 호출 입력 토큰이 얼마인가
- 도구 선택: 어떤 도구를 어떤 순서로 불렀나, 인자 오류·없는 도구 호출이 있었나
- 결과: 저장했나, 본문 근거 id가 실제로 본 기록인가

    python s3_multi/00_many_tools_limit.py [--runs 2]
"""

from __future__ import annotations

import argparse
import json
import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from common import gw_tools, monthly_report
from common.doc_tools import doc_search
from common.runlog import RunLog

REQUEST = ("이재휘의 2026년 10월 월간 업무 보고를 그룹웨어 기록(메일·일정·결재)을 근거로 작성해 저장해 줘. "
           "주요 업무, 장애와 대응, 결재 진행, 다음 달 계획을 포함하고, 문장마다 [mail:12]처럼 근거 id를 달아 줘.")

SYSTEM = """너는 월간 업무 보고를 쓰는 비서다. 도구를 한 번에 하나씩 쓴다.
그룹웨어 도구로 기록을 확인하고, 확인한 기록만 근거로 쓴다. 문장마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다.
다 썼으면 save_monthly_report로 저장하고 한 문장으로 끝났다고 답한다."""

TOOLS = [*gw_tools.DATA_TOOLS, doc_search, gw_tools.save_monthly_report]
MAX_STEPS = 20


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    context: Annotated[list[dict], operator.add]


def build_graph(llm):
    model = llm.bind_tools(TOOLS)

    def agent(state: State):
        resp = model.invoke(state["messages"])
        return {"messages": [resp], "context": [{"call": len(state.get("context", [])) + 1,
                                                 "input_tokens": (resp.usage_metadata or {}).get("input_tokens"),
                                                 "chars": sum(len(str(m.content)) for m in state["messages"])}]}

    def route(state: State) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and len(state["context"]) < MAX_STEPS:
            return "tools"
        return END

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", END])
    g.add_edge("tools", "agent")
    return g.compile()


def inputs(request: str = REQUEST) -> dict:
    return {"messages": [SystemMessage(SYSTEM), HumanMessage(request)]}


def analyze(state: dict) -> dict:
    msgs = state["messages"]
    names = {t.name for t in TOOLS}
    calls = [{"name": c["name"], "args": c["args"]} for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
    errors = [m.content[:160] for m in msgs if isinstance(m, ToolMessage) and str(m.content).startswith("Error")]
    seen = {r for m in msgs if isinstance(m, ToolMessage) for r in monthly_report.refs_loose(str(m.content))}
    saves = [c["args"] for c in calls if c["name"] == "save_monthly_report"]
    cited = [r for s in (saves[-1].get("sections", []) if saves else []) for r in monthly_report.refs(str(s.get("body", "")))]
    return {"calls": [c["name"] for c in calls], "unknown_tools": [c["name"] for c in calls if c["name"] not in names],
            "tool_errors": errors, "saved": bool(saves), "seen_refs": len(seen), "cited": len(cited),
            "cited_not_seen": sorted(set(cited) - seen), "context": state.get("context", [])}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args(argv)
    msdf.connect()
    graph = build_graph(get_llm())
    for i in range(1, args.runs + 1):
        log = RunLog("s3_00_many_tools_limit", meta={"run": i, "tools": len(TOOLS), "request": REQUEST})
        state = log.stream(graph, inputs(), {"recursion_limit": 60})
        r = analyze(state)
        log.write("analysis", r)
        print(json.dumps({k: r[k] for k in ("calls", "unknown_tools", "saved", "cited", "cited_not_seen")}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
