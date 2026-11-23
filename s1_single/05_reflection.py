"""1단계-05 Reflection · Self-Correction.

    START → write → review ─┬▶ save ─▶ END
                 ▲          ├▶ revise ─▶ write
                 └──────────┘└▶ hand_over ─▶ END   (수정 2번으로도 남은 문제는 사람에게)

점검을 둘로 나눠 회차마다 기록한다.
- 코드 점검: 빠진 칸·분량(양식 검사), 메모에 없는 숫자
- 자기 점검: 같은 모델이 자기 초안을 메모와 비교
코드는 찾았는데 자기 점검은 통과시킨 경우를 missed_by_self로 남긴다.
기본 입력은 다음 주 계획이 없는 짧은 메모다.

    python s1_single/05_reflection.py [메모 파일] [weekly|trip]
"""

from __future__ import annotations

import operator
import re
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import config, docx_report, memo
from common.runlog import RunLog

MAX_REVISIONS = 2


class SelfReview(BaseModel):
    ok: bool = Field(description="초안이 메모와 맞고 고칠 곳이 없으면 true")
    problems: list[str] = Field(default_factory=list, description="고칠 곳. 칸 이름과 이유")


class State(TypedDict, total=False):
    memo: str
    template: str
    draft: dict
    round: int
    feedback: str
    reviews: Annotated[list[dict], operator.add]
    problems: list[str]
    outcome: str        # saved | hand_over
    saved_path: str


def numbers_not_in_memo(draft: dict, memo: str) -> list[str]:
    """메모에 없는 숫자. 올해 연도는 허용하고, 같은 숫자는 한 번만 지적한다."""
    allowed = {int(n) for n in re.findall(r"\d+", memo)} | {date.today().year}
    seen, out = set(), []
    for key, value in draft.items():
        for n in re.findall(r"\d+", str(value)):
            v = int(n)
            if v not in allowed and v not in seen:
                seen.add(v)
                out.append(f"'{key}' 칸에 메모에 없는 숫자 '{n}'이 있습니다.")
    return out


def code_check(template: str, draft: dict, memo: str) -> list[str]:
    return docx_report.check(template, draft) + numbers_not_in_memo(draft, memo)


WRITE_SYSTEM = "너는 회사 보고서를 작성한다. 메모에 있는 내용만으로 칸을 채운다. 메모에 없는 사실·숫자·날짜는 만들지 않는다."
REVIEW_SYSTEM = ("너는 보고서 검토자다. 초안의 각 칸이 메모와 맞는지 확인한다. "
                 "메모에 없는 사실·숫자·날짜, 빠진 내용, 양식 기준에 어긋난 곳을 problems에 적는다. 문제가 없으면 ok=true.")


def build_graph(llm):
    def write(state: State):
        t = state["template"]
        schema = docx_report.fields_model(t)
        ask = f"{docx_report.describe_fields(t)}\n\n[메모]\n{state['memo']}"
        if state.get("feedback"):
            ask += f"\n\n[이전 초안]\n{state['draft']}\n\n[고칠 점]\n{state['feedback']}"
        out = llm.with_structured_output(schema).invoke([SystemMessage(WRITE_SYSTEM), HumanMessage(ask)])
        return {"draft": schema.model_validate(out).model_dump(), "round": state.get("round", 0) + 1}

    def review(state: State):
        code = code_check(state["template"], state["draft"], state["memo"])
        me = SelfReview.model_validate(llm.with_structured_output(SelfReview).invoke([
            SystemMessage(REVIEW_SYSTEM),
            HumanMessage(f"{docx_report.describe_fields(state['template'])}\n\n[메모]\n{state['memo']}\n\n[초안]\n{state['draft']}")]))
        rec = {"round": state["round"], "code": code, "self_ok": me.ok, "self": me.problems,
               "missed_by_self": bool(code) and me.ok}
        return {"reviews": [rec], "problems": code + ([] if me.ok else me.problems)}

    def route(state: State) -> str:
        if not state["problems"]:
            return "save"
        return "revise" if state["round"] <= MAX_REVISIONS else "hand_over"

    def revise(state: State):
        return {"feedback": "\n".join(f"- {p}" for p in state["problems"])}

    def save(state: State):
        path = docx_report.save(state["template"], state["draft"], stem=f"{state['template']}_reflection")
        return {"outcome": "saved", "saved_path": str(path)}

    def hand_over(state: State):
        return {"outcome": "hand_over"}   # 저장하지 않는다. 남은 문제는 problems에

    g = StateGraph(State)
    for name, fn in [("write", write), ("review", review), ("revise", revise), ("save", save), ("hand_over", hand_over)]:
        g.add_node(name, fn)
    g.add_edge(START, "write")
    g.add_edge("write", "review")
    g.add_conditional_edges("review", route, ["save", "revise", "hand_over"])
    g.add_edge("revise", "write")
    g.add_edge("save", END)
    g.add_edge("hand_over", END)
    return g.compile()


def inputs(memo: str, template: str) -> dict:
    return {"memo": memo, "template": template, "round": 0}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    memo_path = Path(argv[0]) if argv else config.SAMPLE_DIR / "memo_short.txt"
    template = argv[1] if len(argv) > 1 else "weekly"
    log = RunLog("s1_05_reflection", meta={"memo": memo_path.name, "template": template})
    state = log.stream(build_graph(get_llm()), inputs(memo.load(memo_path), template))
    log.write("end", {k: state.get(k) for k in ("outcome", "round", "problems", "saved_path", "draft")})


if __name__ == "__main__":
    main(sys.argv[1:])
