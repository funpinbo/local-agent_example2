"""1단계-02 ReAct Loop.

    START → agent ⇄ tools → END   (01과 같은 모양, 도구 4개)

fill_field로 한 칸씩 채우며 생각 → 행동 → 관찰을 반복한다.
채운 값은 messages가 아니라 State의 draft에 쌓인다(칸 단위로 덮어쓰는 reducer).
LLM을 부를 때마다 받은 메시지 수와 글자 수를 context에 남겨, 루프가 돌수록 입력이 늘어나는 것을 본다.

    python s1_single/02_react_loop.py [메모 파일] [weekly|trip]
"""

from __future__ import annotations

import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from common import config, memo
from common.report_tools import fill_field, list_template_fields, save_draft, show_draft
from common.runlog import RunLog
from common.templates.specs import SPECS


def merge_draft(old: dict | None, new: dict | None) -> dict:
    """칸 단위로 덮어쓴다. 같은 칸을 다시 채우면 새 값이 남는다."""
    return {**(old or {}), **(new or {})}


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    template: str
    draft: Annotated[dict, merge_draft]
    saved_path: str
    context: Annotated[list[dict], operator.add]   # LLM 호출마다 입력 크기


SYSTEM = """너는 회사 보고서를 작성하는 비서다. 도구를 한 번에 하나씩 쓴다.
1. list_template_fields로 칸과 작성 기준을 확인한다.
2. fill_field로 칸을 하나씩 채운다. 메모에 있는 내용만 쓰고, 메모에 없는 사실·숫자·날짜는 만들지 않는다.
3. 다 채웠으면 show_draft로 확인하고 save_draft로 저장한다. 저장이 거절되면 알려 준 칸을 고친다.
4. 저장했으면 한 문장으로 끝났다고 답한다."""

TOOLS = [list_template_fields, fill_field, show_draft, save_draft]


def build_graph(llm):
    model = llm.bind_tools(TOOLS)

    def agent(state: State):
        msgs = state["messages"]
        size = {"call": len(state.get("context", [])) + 1, "messages": len(msgs),
                "chars": sum(len(str(m.content)) for m in msgs)}
        return {"messages": [model.invoke(msgs)], "context": [size]}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile()


def inputs(memo: str, template: str) -> dict:
    spec = SPECS[template]
    return {"template": template, "draft": {}, "messages": [
        SystemMessage(SYSTEM),
        HumanMessage(f"아래 메모로 '{spec.title}'(양식 이름 {template}) 보고서를 작성해 저장해 줘.\n\n{memo}"),
    ]}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    memo_path = Path(argv[0]) if argv else config.SAMPLE_DIR / "memo_weekly.txt"
    template = argv[1] if len(argv) > 1 else "weekly"
    log = RunLog("s1_02_react_loop", meta={"memo": memo_path.name, "template": template})
    state = log.stream(build_graph(get_llm()), inputs(memo.load(memo_path), template))
    log.write("end", {"saved_path": state.get("saved_path"), "draft": state.get("draft"),
                      "context": state.get("context")})


if __name__ == "__main__":
    main(sys.argv[1:])
