"""1단계-01 기본 Tool Calling Agent.

    START → agent ⇄ tools → END

LLM Node, ToolNode, Conditional Edge(tools_condition), 되돌아가는 Edge를 직접 조립한다.
(langchain.agents.create_agent가 같은 모양을 한 줄로 만들어 준다.)

    python s1_single/01_tool_calling.py [메모 파일] [weekly|trip]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from common import config, memo
from common.report_tools import list_template_fields, save_report
from common.runlog import RunLog
from common.templates.specs import SPECS

SYSTEM = """너는 회사 보고서를 작성하는 비서다.
1. 먼저 list_template_fields로 양식의 칸과 작성 기준을 확인한다.
2. 메모에 있는 내용만으로 모든 칸을 채워 save_report로 저장한다. 메모에 없는 사실·숫자·날짜는 만들지 않는다.
3. 저장 결과를 확인하고, 저장에 실패하면 알려 준 문제를 고쳐 다시 저장한다.
4. 저장했으면 한 문장으로 끝났다고 답한다."""

TOOLS = [list_template_fields, save_report]


def build_graph(llm):
    model = llm.bind_tools(TOOLS)

    def agent(state: MessagesState):
        return {"messages": [model.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)   # 도구 호출이 있으면 tools, 없으면 END
    g.add_edge("tools", "agent")
    return g.compile()


def inputs(memo: str, template: str) -> dict:
    spec = SPECS[template]
    return {"messages": [
        SystemMessage(SYSTEM),
        HumanMessage(f"아래 메모로 '{spec.title}' 보고서를 작성해 저장해 줘. 양식 이름은 {template}이다.\n\n{memo}"),
    ]}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    memo_path = Path(argv[0]) if argv else config.SAMPLE_DIR / "memo_weekly.txt"
    template = argv[1] if len(argv) > 1 else "weekly"
    log = RunLog("s1_01_tool_calling", meta={"memo": memo_path.name, "template": template})
    state = log.stream(build_graph(get_llm()), inputs(memo.load(memo_path), template))
    log.write("end", {"messages": len(state["messages"]), "last": state["messages"][-1].content})


if __name__ == "__main__":
    main(sys.argv[1:])
