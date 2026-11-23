"""3단계에서 여러 번 쓰는 작은 Agent: 자기 도구만 가진 ReAct 루프 + 멈춤 장치.

1단계 01·02와 같은 모양(agent ⇄ tools)이고, 04에서 배운 대로 LLM 호출 상한을 둔다.
일을 받은 Agent의 결과물은 '글'(정리한 메모)이다. 1단계에서 문제였던 "도구 대신 글로 답하고 끝내기"가
여기서는 정상 종료가 된다.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from common import monthly_report


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int


def react_agent(llm, tools: list, system: str, *, max_steps: int = 8):
    """도구를 가진 작은 Agent. 입력 {"messages": [HumanMessage(일)]}, 출력 messages."""
    model = llm.bind_tools(tools)

    def think(state: AgentState):
        msgs = [SystemMessage(system), *state["messages"]]
        return {"messages": [model.invoke(msgs)], "steps": state.get("steps", 0) + 1}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state["steps"] < max_steps:
            return "tools"
        return END

    g = StateGraph(AgentState)
    g.add_node("think", think)
    g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    g.add_edge(START, "think")
    g.add_conditional_edges("think", route, ["tools", END])
    g.add_edge("tools", "think")
    return g.compile()


def run_agent(graph, task: str) -> dict:
    """일을 시키고 결과 메모·사용한 도구·본 근거 id를 돌려준다."""
    out = graph.invoke({"messages": [HumanMessage(task)], "steps": 0}, {"recursion_limit": 40})
    msgs = out["messages"]
    last = msgs[-1]
    note = last.content if isinstance(last, AIMessage) and not last.tool_calls else ""
    tools_used = [c["name"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
    seen = sorted({r for m in msgs if isinstance(m, ToolMessage) for r in monthly_report.refs_loose(str(m.content))})
    return {"note": note, "tools": tools_used, "seen": seen, "steps": out.get("steps", 0),
            "messages": msgs, "input_tokens": [(m.usage_metadata or {}).get("input_tokens")
                                               for m in msgs if isinstance(m, AIMessage)]}
