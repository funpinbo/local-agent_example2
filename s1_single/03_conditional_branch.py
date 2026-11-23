"""1단계-03 Conditional Branch.

    START ─(코드: 양식이 주어졌나)─▶ guide ─▶ agent
          └──────────────────────▶ agent
    agent ─(LLM 출력: 어떤 도구를 불렀나)─▶ select ─▶ guide ─▶ agent
                                        ├▶ tools ─▶ agent
                                        └▶ END

양식이 주어지면 코드가 바로 경로를 정해 LLM에게 묻지 않는다.
양식이 없으면 LLM이 select_template으로 고르고, 그 출력으로 경로가 갈라진다.
갈라진 뒤 양식별 규칙은 코드 노드(guide)가 넣는다.

    python s1_single/03_conditional_branch.py                                         # LLM이 양식 선택
    python s1_single/03_conditional_branch.py common/sample_data/memo_trip.txt trip  # 코드가 경로 결정
"""

from __future__ import annotations

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

from common import config, docx_report, memo
from common.report_tools import fill_field, save_draft, show_draft
from common.runlog import RunLog


def merge_draft(old, new):
    return {**(old or {}), **(new or {})}


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    template: str | None
    draft: Annotated[dict, merge_draft]
    saved_path: str
    route: str          # 어느 쪽이 경로를 정했나: code | llm


@tool
def select_template(template: str, runtime: ToolRuntime) -> Command:
    """메모 내용에 맞는 보고서 양식을 고른다. 주간 업무 메모면 'weekly', 출장 다녀온 메모면 'trip'."""
    if template not in ("weekly", "trip"):
        msg = f"'{template}'는 없는 양식입니다. weekly 또는 trip 중에 고르세요."
        return Command(update={"messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id, name="select_template")]})
    return Command(update={"template": template, "messages": [
        ToolMessage(f"양식을 {template}로 정했습니다.", tool_call_id=runtime.tool_call_id, name="select_template")]})


RULES = {
    "weekly": "- 다음 주 계획은 메모에 적힌 것만 쓴다. 없으면 '메모에 계획 없음'.\n- 사람 이름은 메모에 적힌 그대로 쓴다.",
    "trip": "- 출장비·교통비 같은 비용은 쓰지 않는다(정산은 별도 시스템).\n"
            "- 후속 조치는 메모의 항목을 모두 쓰고, 항목마다 '누가 · 언제까지 · 무엇을'로 쓴다. "
            "메모에 담당자나 기한이 없으면 그 자리에 '미정'이라고 쓴다.",
}

# 프롬프트에도 그 경로에서 쓸 수 있는 도구만 적는다.
# (도구 목록에서 select_template을 뺐는데 프롬프트에 이름이 남아 있자, LLM이 없는 도구를 계속 불렀다)
SYSTEM = """너는 회사 보고서를 작성하는 비서다. 도구를 한 번에 하나씩 쓴다.
메모에 없는 사실·숫자·날짜는 만들지 않는다."""
CHOOSE = "먼저 select_template으로 메모에 맞는 양식을 고른다."
WORK = ("안내받은 칸을 fill_field로 하나씩 채우고, show_draft로 확인한 뒤 save_draft로 저장한다. "
        "사용자에게 되묻지 말고 메모로 채운다. 저장했으면 한 문장으로 끝났다고 답한다.")

WORK_TOOLS = [fill_field, show_draft, save_draft]


def build_graph(llm):
    # 양식이 정해지기 전에는 고르는 도구만, 정해진 뒤에는 채우는 도구만 보여 준다.
    # (처음엔 둘 다 줬더니, 코드가 양식을 정한 경로에서도 LLM이 select_template을 다시 불렀다)
    choose = llm.bind_tools([select_template])
    work = llm.bind_tools(WORK_TOOLS)

    def agent(state: State):
        model = work if state.get("template") else choose
        return {"messages": [model.invoke(state["messages"])]}

    def guide(state: State):
        t = state["template"]
        text = f"[양식 안내] {docx_report.describe_fields(t)}\n\n[이 양식의 규칙]\n{RULES[t]}\n\n{WORK}"
        return {"messages": [HumanMessage(text)]}

    # 분기 1: 코드가 입력만 보고 정한다 (LLM 호출 없음)
    def route_start(state: State) -> str:
        return "guide" if state.get("template") else "agent"

    # 분기 2: LLM이 낸 출력(어떤 도구를 불렀나)으로 갈라진다
    def route_after_agent(state: State) -> str:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return END
        return "select" if last.tool_calls[0]["name"] == "select_template" else "tools"

    def route_after_select(state: State) -> str:
        return "guide" if state.get("template") else "agent"

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("select", ToolNode([select_template]))
    g.add_node("tools", ToolNode(WORK_TOOLS))
    g.add_node("guide", guide)
    g.add_conditional_edges(START, route_start, ["guide", "agent"])
    g.add_conditional_edges("agent", route_after_agent, ["select", "tools", END])
    g.add_conditional_edges("select", route_after_select, ["guide", "agent"])
    g.add_edge("tools", "agent")
    g.add_edge("guide", "agent")
    return g.compile()


def inputs(memo: str, template: str | None) -> dict:
    ask = "아래 메모로 보고서를 작성해 저장해 줘."
    system = SYSTEM if template else f"{SYSTEM}\n{CHOOSE}"
    return {"template": template, "draft": {}, "route": "code" if template else "llm",
            "messages": [SystemMessage(system), HumanMessage(f"{ask}\n\n{memo}")]}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    memo_path = Path(argv[0]) if argv else config.SAMPLE_DIR / "memo_trip.txt"
    template = argv[1] if len(argv) > 1 else None
    log = RunLog("s1_03_conditional_branch", meta={"memo": memo_path.name, "template": template})
    state = log.stream(build_graph(get_llm()), inputs(memo.load(memo_path), template))
    log.write("end", {"route": state.get("route"), "template": state.get("template"),
                      "saved_path": state.get("saved_path"), "draft": state.get("draft")})


if __name__ == "__main__":
    main(sys.argv[1:])
