"""3단계-02 Handoff (작성 → 수집 → 작성).

    START → writer ⇄ writer_tools ─(transfer_to_collector)─▶ collector ⇄ collector_tools ─(transfer_to_writer)─▶ writer …

Agent가 스스로 다음 Agent에게 넘긴다. 넘기는 것도 도구 호출(transfer_to_*)이고, 그 결과로 State의 active가 바뀐다.
2단계 Routing은 코드가 경로를 정했다. 여기서는 Agent가 정한다.
두 Agent는 대화(messages)를 함께 쓰고, 부를 때마다 자기 시스템 프롬프트만 앞에 붙인다.

    python s3_multi/02_handoff.py
"""

from __future__ import annotations

import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from typing_extensions import TypedDict

from common import gw_tools, monthly_report
from common.runlog import RunLog

REQUEST = "이재휘의 2026년 10월 월간 업무 보고를 그룹웨어 기록(메일·일정·결재)을 근거로 작성해 저장해 줘."
MAX_STEPS = 24


@tool
def transfer_to_collector(what: str, runtime: ToolRuntime) -> Command:
    """그룹웨어 기록 수집 담당에게 일을 넘긴다. what에는 무엇을 모아 와야 하는지 쓴다."""
    return Command(update={"active": "collector", "handoffs": [{"to": "collector", "what": what}],
                           "messages": [ToolMessage(f"수집 담당에게 넘겼습니다: {what}", tool_call_id=runtime.tool_call_id,
                                                    name="transfer_to_collector")]})


@tool
def transfer_to_writer(summary: str, runtime: ToolRuntime) -> Command:
    """보고서 작성 담당에게 돌려준다. summary에는 모은 기록을 항목별로, 항목마다 [mail:12]처럼 근거 id를 달아 쓴다."""
    return Command(update={"active": "writer", "handoffs": [{"to": "writer", "summary_chars": len(summary)}],
                           "messages": [ToolMessage(f"수집 결과:\n{summary}", tool_call_id=runtime.tool_call_id,
                                                    name="transfer_to_writer")]})


WRITER_TOOLS = [transfer_to_collector, gw_tools.save_monthly_report]
COLLECTOR_TOOLS = [gw_tools.gw_list_mails, gw_tools.gw_read_mail, gw_tools.gw_list_events,
                   gw_tools.gw_list_approvals, gw_tools.gw_read_approval, transfer_to_writer]

WRITER_SYSTEM = """너는 월간 보고 작성 담당이다. 도구를 한 번에 하나씩 쓴다.
그룹웨어 기록은 직접 보지 못한다. 자료가 없으면 transfer_to_collector로 수집 담당에게 무엇이 필요한지 넘긴다.
수집 결과를 받으면 그 내용만 근거로 save_monthly_report로 저장한다. 문장마다 [mail:12]처럼 근거 id를 단다."""
COLLECTOR_SYSTEM = """너는 그룹웨어 기록 수집 담당이다. 도구를 한 번에 하나씩 쓴다.
요청받은 기록을 그룹웨어 도구로 확인하고, 다 모았으면 transfer_to_writer로 작성 담당에게 돌려준다.
돌려줄 때 항목마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다."""


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    active: str
    steps: int
    handoffs: Annotated[list[dict], operator.add]


def build_graph(llm):
    models = {"writer": (llm.bind_tools(WRITER_TOOLS), WRITER_SYSTEM),
              "collector": (llm.bind_tools(COLLECTOR_TOOLS), COLLECTOR_SYSTEM)}

    def make_agent(name: str):
        model, system = models[name]

        def think(state: State):
            resp = model.invoke([SystemMessage(system), *state["messages"]])
            resp.name = name            # 누가 말했는지 남긴다
            return {"messages": [resp], "steps": state.get("steps", 0) + 1}
        return think

    def after_think(state: State) -> str:
        last = state["messages"][-1]
        if state["steps"] >= MAX_STEPS or not (isinstance(last, AIMessage) and last.tool_calls):
            return END
        return f"{state['active']}_tools"

    def after_tools(state: State) -> str:
        return state["active"]          # 넘기는 도구가 active를 바꿨으면 다른 Agent로 간다

    g = StateGraph(State)
    g.add_node("writer", make_agent("writer"))
    g.add_node("collector", make_agent("collector"))
    g.add_node("writer_tools", ToolNode(WRITER_TOOLS, handle_tool_errors=True))
    g.add_node("collector_tools", ToolNode(COLLECTOR_TOOLS, handle_tool_errors=True))
    g.add_edge(START, "writer")
    for n in ("writer", "collector"):
        g.add_conditional_edges(n, after_think, ["writer_tools", "collector_tools", END])
        g.add_conditional_edges(f"{n}_tools", after_tools, ["writer", "collector"])
    return g.compile()


def inputs(request: str = REQUEST) -> dict:
    return {"messages": [HumanMessage(request)], "active": "writer", "steps": 0}


def analyze(state: dict) -> dict:
    msgs = state["messages"]
    calls = [(m.name, c["name"]) for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
    errors = [str(m.content)[:160] for m in msgs if isinstance(m, ToolMessage) and str(m.content).startswith("Error")]
    seen = {r for m in msgs if isinstance(m, ToolMessage) and m.name != "transfer_to_writer"
            for r in monthly_report.refs_loose(str(m.content))}
    saves = [c["args"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] == "save_monthly_report"]
    cited = [r for s in (saves[-1].get("sections", []) if saves else []) for r in monthly_report.refs(str(s.get("body", "")))]
    return {"calls": calls, "handoffs": state.get("handoffs", []), "tool_errors": errors, "saved": bool(saves),
            "steps": state.get("steps"), "cited": len(cited), "cited_not_seen": sorted(set(cited) - seen),
            "ended_by": msgs[-1].name if isinstance(msgs[-1], AIMessage) else None}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    log = RunLog("s3_02_handoff", meta={"request": REQUEST, "max_steps": MAX_STEPS})
    state = log.stream(build_graph(get_llm()), inputs(), {"recursion_limit": 80})
    log.write("analysis", analyze(state))


if __name__ == "__main__":
    main(sys.argv[1:])
