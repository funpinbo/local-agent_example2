"""4단계-04 Human-in-the-loop (결재 상신 직전 interrupt).

    START → compose → save_docx → prepare → human ─┬▶ submit → END        (approve)
                                       ▲           ├▶ apply_edit ─┘       (edit: 고친 뒤 다시 human)
                                       └───────────┘└▶ END                (reject: 상신하지 않음)

보고서를 docx로 만들고 결재 상신 문서(제목·결재선·본문 요약)를 준비한 뒤, interrupt로 멈춰 사람의 결정을 기다린다.
Checkpointer가 멈춘 자리(State와 다음 노드)를 저장하고, Command(resume=...)로 이어서 실행한다(2편 2(2)의 사용자 승인 흐름).
상신은 사람이 approve한 뒤에만 그룹웨어 API로 한다. 에이전트가 직접 결재를 올리는 일은 되돌리기 어렵기 때문이다.

    python s4_graph/04_human_in_the_loop.py [--month 2026-10] [--auto approve|reject]
"""

from __future__ import annotations

import argparse
import json
import operator
import sys
import uuid
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from common import approval, gw_tools, monthly_report, sections
from common.runlog import RunLog

LINE = approval.LINE
SOURCES = {"주요 업무": ["mails", "events"], "장애와 대응": ["mails"], "결재 진행": ["approvals"],
           "다음 달 계획": ["mails", "events"]}


class State(TypedDict, total=False):
    month: str
    digest: dict
    sections: list[dict]
    docx_path: str
    draft: dict                    # 상신할 결재 문서
    decisions: Annotated[list[dict], operator.add]
    submitted: dict
    outcome: str


def title(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월 월간 업무 보고 (이재휘)"


def build_graph(llm, checkpointer=None):
    def compose(state: State):
        d = gw_tools.digest(state["month"])
        done: list[dict] = []
        for h in sections.SECTIONS:
            items = sections.items_of(d, SOURCES[h])
            body = (sections.write_section(llm, title(state["month"]), h, items, done) if items
                    else f"{state['month']} 기간에 해당 기록이 없다.")
            done.append({"heading": h, "body": body})
        return {"digest": d, "sections": done}

    def save_docx(state: State):
        return {"docx_path": str(monthly_report.save(title(state["month"]), state["sections"], stem=f"s4_04_hitl_{state['month']}"))}

    def prepare(state: State):
        known = {i["ref"] for i in state["digest"]["items"]}
        problems = {s["heading"]: monthly_report.check_refs(s["body"], known) for s in state["sections"]}
        summary = " / ".join(f"{s['heading']}: {s['body'][:60]}…" for s in state["sections"])
        return {"draft": {"kind": "월간보고", "title": title(state["month"]), "line": LINE,
                          "body": f"{state['month']} 월간 업무 보고를 첨부합니다. 요약: {summary}",
                          "attachment": Path(state["docx_path"]).name,
                          "check": {h: p for h, p in problems.items() if p}}}

    def human(state: State):
        # 여기서 멈춘다. 다시 실행되면 interrupt()가 사람이 준 값을 돌려준다.
        answer = interrupt({"question": "이 문서로 결재를 상신할까요? (approve / edit / reject)", "draft": state["draft"]})
        return {"decisions": [answer if isinstance(answer, dict) else {"action": str(answer)}]}

    def route(state: State) -> str:
        a = state["decisions"][-1].get("action")
        return {"approve": "submit", "edit": "apply_edit"}.get(a, "rejected")

    def apply_edit(state: State):
        ch = state["decisions"][-1].get("changes", {})
        return {"draft": {**state["draft"], **{k: v for k, v in ch.items() if k in ("title", "line", "body")}}}

    def submit(state: State):
        return {"submitted": approval.submit(state["draft"], state["docx_path"]), "outcome": "submitted"}

    def rejected(state: State):
        return {"outcome": "not_submitted"}

    g = StateGraph(State)
    for n, fn in [("compose", compose), ("save_docx", save_docx), ("prepare", prepare), ("human", human),
                  ("apply_edit", apply_edit), ("submit", submit), ("rejected", rejected)]:
        g.add_node(n, fn)
    g.add_edge(START, "compose")
    g.add_edge("compose", "save_docx")
    g.add_edge("save_docx", "prepare")
    g.add_edge("prepare", "human")
    g.add_conditional_edges("human", route, ["submit", "apply_edit", "rejected"])
    g.add_edge("apply_edit", "human")
    g.add_edge("submit", END)
    g.add_edge("rejected", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def ask_terminal(payload: dict) -> dict:
    print("\n" + json.dumps(payload, ensure_ascii=False, indent=1))
    a = input("approve / edit / reject > ").strip() or "reject"
    if a == "edit":
        t = input("새 제목(빈칸이면 그대로) > ").strip()
        return {"action": "edit", "changes": {"title": t} if t else {}}
    return {"action": a}


def run(graph, month: str, decide, log: RunLog) -> dict:
    cfg = {"configurable": {"thread_id": f"hitl-{uuid.uuid4().hex[:8]}"}, "recursion_limit": 40}
    log.stream(graph, {"month": month}, cfg)
    while True:
        snap = graph.get_state(cfg)
        if not snap.next:            # 끝났다
            return snap.values
        payload = snap.tasks[0].interrupts[0].value
        log.write("interrupt", {"next": snap.next, "draft": payload["draft"]})
        answer = decide(payload)
        log.write("human", answer)
        log.stream(graph, Command(resume=answer), cfg)


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-10")
    ap.add_argument("--auto", choices=["approve", "reject"])
    args = ap.parse_args(argv)
    msdf.connect()
    decide = (lambda p: {"action": args.auto}) if args.auto else ask_terminal
    log = RunLog("s4_04_human_in_the_loop", meta={"month": args.month, "auto": args.auto})
    state = run(build_graph(get_llm()), args.month, decide, log)
    log.write("end", {k: state.get(k) for k in ("outcome", "decisions", "submitted", "docx_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
