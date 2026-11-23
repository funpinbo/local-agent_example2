"""2단계-00 먼저 RAG 도구만 붙여 보기 (1단계 Single Agent 그대로 + 문서 검색 도구).

    START → agent ⇄ tools → END        도구: doc_search, save_tech_report

같은 요청을 여러 번 돌려 기록한다.
- 검색 순서: LLM이 무엇을 어떤 순서로 찾았나 (실행마다 같은가)
- 컨텍스트 누적: 검색 결과가 messages에 계속 쌓여 LLM 입력이 얼마나 커지나
- 근거 섞임: 섹션이 인용한 조각이 실제로 찾아온 조각인가, 어느 검색에서 온 조각인가

    python s2_workflow/00_rag_tool_limit.py [--runs 3]
"""

from __future__ import annotations

import argparse
import json
import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from common import tech_report
from common.doc_tools import doc_search
from common.runlog import RunLog

REQUEST = "로컬 LLM 서버 도입 한 달의 운영 현황을 사내 문서를 근거로 기술 보고서로 정리해 줘. 장애와 대응, 운영 정책, 앞으로의 계획을 포함해 줘."

SYSTEM = """너는 사내 기술 보고서를 쓰는 비서다. 도구를 한 번에 하나씩 쓴다.
1. doc_search로 필요한 사내 문서 조각을 찾는다. 여러 번 찾아도 된다.
2. 찾은 조각만 근거로 보고서를 쓴다. 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다). 찾지 않은 내용은 쓰지 않는다.
3. save_tech_report로 제목과 섹션 목록을 저장한다. 저장했으면 한 문장으로 끝났다고 답한다."""


def retrieved_ids(messages: list) -> dict[str, int]:
    """조각 id → 몇 번째 검색에서 처음 나왔나."""
    out: dict[str, int] = {}
    n = 0
    for m in messages:
        if isinstance(m, ToolMessage) and m.name == "doc_search":
            n += 1
            try:
                for h in json.loads(m.content):
                    out.setdefault(h["chunk_id"], n)
            except (json.JSONDecodeError, TypeError):
                pass
    return out


@tool
def save_tech_report(title: str, sections: list[dict], runtime: ToolRuntime) -> str:
    """기술 보고서를 docx로 저장한다. sections는 [{"heading": 섹션 제목, "body": 본문}] 목록이다. 본문 문장마다 [chunk_id] 인용을 단다."""
    secs = [tech_report.Section(**s) for s in sections]
    got = retrieved_ids(runtime.state["messages"])
    path = tech_report.save_report(title, secs, stem="s2_00_rag_tool")
    return f"저장했습니다: {path.name} (섹션 {len(secs)}개, 인용 {sum(len(tech_report.citations(s.body)) for s in secs)}개, 찾아온 조각 {len(got)}개)"


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    context: Annotated[list[dict], operator.add]


TOOLS = [doc_search, save_tech_report]


def build_graph(llm):
    model = llm.bind_tools(TOOLS)

    def agent(state: State):
        msgs = state["messages"]
        resp = model.invoke(msgs)
        tokens = (resp.usage_metadata or {}).get("input_tokens")
        size = {"call": len(state.get("context", [])) + 1, "messages": len(msgs),
                "chars": sum(len(str(m.content)) for m in msgs), "input_tokens": tokens}
        return {"messages": [resp], "context": [size]}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile()


def inputs(request: str = REQUEST) -> dict:
    return {"messages": [SystemMessage(SYSTEM), HumanMessage(request)]}


def analyze(state: dict) -> dict:
    """한 실행의 검색 순서, 입력 크기, 인용 상태."""
    msgs = state["messages"]
    queries = [c["args"].get("query") for m in msgs if isinstance(m, AIMessage)
               for c in m.tool_calls if c["name"] == "doc_search"]
    got = retrieved_ids(msgs)
    saves = [c["args"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] == "save_tech_report"]
    sections = []
    if saves:
        for s in saves[-1].get("sections", []):
            cited = tech_report.citations(s.get("body", ""))
            sections.append({"heading": s.get("heading"), "cited": cited,
                             "not_retrieved": sorted(set(cited) - set(got)),
                             "from_searches": sorted({got[c] for c in cited if c in got})})
    return {"queries": queries, "retrieved": len(got), "saved": bool(saves),
            "context": state.get("context", []), "sections": sections}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)
    msdf.connect()
    graph = build_graph(get_llm())
    summary = []
    for i in range(1, args.runs + 1):
        log = RunLog("s2_00_rag_tool_limit", meta={"run": i, "request": REQUEST})
        state = log.stream(graph, inputs())
        result = analyze(state)
        log.write("analysis", result)
        summary.append({"run": i, "queries": result["queries"], "saved": result["saved"],
                        "last_input_tokens": (result["context"] or [{}])[-1].get("input_tokens")})
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
