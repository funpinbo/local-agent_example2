"""1단계-04 Retry · Fallback · Stop.

    START → agent ─┬▶ tools ─▶ agent
                   ├▶ retry ─▶ agent         (형식이 틀린 도구 호출, 본문에 JSON/호출을 글자로 씀, 저장 없이 끝냄)
                   ├▶ fallback ─▶ END        (retry 2번으로도 안 되면 구조화 출력으로 칸 전체를 받아 코드가 저장)
                   ├▶ stop ─▶ END            (LLM 16번 초과, 같은 호출 3번 연속)
                   └▶ finish ─▶ END          (저장한 뒤 끝냄)

LLM 판단에 맡기는 정상 종료와 코드가 강제하는 안전 종료를 두 겹으로 둔다(2편 Agent Loop의 종료 조건).
카운터(steps, retries, calls, outcome)는 모두 State에 있다.

    python s1_single/04_retry_fallback_stop.py [메모 파일] [weekly|trip]
"""

from __future__ import annotations

import json
import operator
import re
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from common import config, docx_report, memo
from common.report_tools import fill_field, list_template_fields, save_draft, show_draft
from common.runlog import RunLog
from common.templates.specs import SPECS

MAX_STEPS = 16       # LLM 호출 상한
MAX_RETRIES = 2      # 형식 오류 재시도 상한
REPEAT_LIMIT = 3     # 같은 호출 연속 상한


def merge_draft(old, new):
    return {**(old or {}), **(new or {})}


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    memo: str
    template: str
    draft: Annotated[dict, merge_draft]
    saved_path: str
    steps: int
    retries: int
    calls: Annotated[list[str], operator.add]
    problem: str
    outcome: str        # done | fallback | fallback_failed | max_steps | repeat


SYSTEM = """너는 회사 보고서를 작성하는 비서다. 도구를 한 번에 하나씩 쓴다.
list_template_fields로 칸을 확인하고, fill_field로 칸을 하나씩 채운 뒤 save_draft로 저장한다.
메모에 없는 사실·숫자·날짜는 만들지 않는다. 저장했으면 한 문장으로 끝났다고 답한다."""

TOOLS = [list_template_fields, fill_field, show_draft, save_draft]
_TEXT_CALL = re.compile(r"(<function=|^\s*[\{\[]|\"name\"\s*:|fill_field\s*\(|save_draft\s*\()", re.M)


def _signature(msg: AIMessage) -> str:
    c = msg.tool_calls[0]
    return f"{c['name']}:{json.dumps(c['args'], ensure_ascii=False, sort_keys=True)}"


def build_graph(llm):
    model = llm.bind_tools(TOOLS)

    def agent(state: State):
        resp = model.invoke(state["messages"])
        update = {"messages": [resp], "steps": state.get("steps", 0) + 1}
        if resp.tool_calls:
            update["calls"] = [_signature(resp)]
        return update

    def route(state: State) -> str:
        last: AIMessage = state["messages"][-1]
        if state["steps"] > MAX_STEPS:
            return "stop"
        if last.tool_calls:
            recent = state.get("calls", [])[-REPEAT_LIMIT:]
            if len(recent) == REPEAT_LIMIT and len(set(recent)) == 1:
                return "stop"
            return "tools"
        if state.get("saved_path"):
            return "finish"
        # 도구를 제대로 부르지 못했다
        return "retry" if state.get("retries", 0) < MAX_RETRIES else "fallback"

    def retry(state: State):
        last: AIMessage = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else ""
        if last.invalid_tool_calls:
            why = f"도구 호출의 인자를 읽지 못했습니다: {last.invalid_tool_calls[0].get('error')}"
        elif _TEXT_CALL.search(text):
            why = "도구를 부르지 않고 호출 내용을 글자로 썼습니다."
        else:
            why = "아직 저장하지 않았는데 답을 끝냈습니다."
        tip = ('도구는 <function=이름>{"인자": "값"}</function> 형식으로 한 줄에 하나만 부릅니다. '
               "인자는 올바른 JSON 객체여야 합니다. 남은 칸을 채우고 save_draft로 저장하세요.")
        return {"retries": state.get("retries", 0) + 1, "problem": why,
                "messages": [HumanMessage(f"[재시도 {state.get('retries', 0) + 1}/{MAX_RETRIES}] {why} {tip}")]}

    def fallback(state: State):
        """도구 루프를 버리고, 칸 전체를 구조화 출력으로 한 번에 받아 코드가 저장한다."""
        template = state["template"]
        schema = docx_report.fields_model(template)
        prompt = [SystemMessage("메모에 있는 내용만으로 보고서 칸을 모두 채운다. 메모에 없는 사실·숫자·날짜는 만들지 않는다."),
                  HumanMessage(f"{docx_report.describe_fields(template)}\n\n[메모]\n{state['memo']}")]
        try:
            fields = schema.model_validate(llm.with_structured_output(schema).invoke(prompt)).model_dump()
            path = docx_report.save(template, {**(state.get("draft") or {}), **fields})
        except Exception as e:  # 구조화 출력도 실패하면 저장하지 않고 끝낸다
            return {"outcome": "fallback_failed", "problem": f"{type(e).__name__}: {e}"}
        return {"outcome": "fallback", "draft": fields, "saved_path": str(path)}

    def stop(state: State):
        why = "max_steps" if state["steps"] > MAX_STEPS else "repeat"
        return {"outcome": why}

    def finish(state: State):
        return {"outcome": "done"}

    g = StateGraph(State)
    for name, fn in [("agent", agent), ("retry", retry), ("fallback", fallback), ("stop", stop), ("finish", finish)]:
        g.add_node(name, fn)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", "retry", "fallback", "stop", "finish"])
    g.add_edge("tools", "agent")
    g.add_edge("retry", "agent")
    for n in ("fallback", "stop", "finish"):
        g.add_edge(n, END)
    return g.compile()


def inputs(memo: str, template: str) -> dict:
    spec = SPECS[template]
    return {"memo": memo, "template": template, "draft": {}, "steps": 0, "retries": 0, "messages": [
        SystemMessage(SYSTEM),
        HumanMessage(f"아래 메모로 '{spec.title}'(양식 이름 {template}) 보고서를 작성해 저장해 줘.\n\n{memo}")]}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    memo_path = Path(argv[0]) if argv else config.SAMPLE_DIR / "memo_weekly.txt"
    template = argv[1] if len(argv) > 1 else "weekly"
    log = RunLog("s1_04_retry_fallback_stop", meta={"memo": memo_path.name, "template": template,
                                                    "max_steps": MAX_STEPS, "max_retries": MAX_RETRIES})
    state = log.stream(build_graph(get_llm()), inputs(memo.load(memo_path), template))
    log.write("end", {k: state.get(k) for k in ("outcome", "steps", "retries", "problem", "saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
