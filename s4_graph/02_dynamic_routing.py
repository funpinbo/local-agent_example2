"""4단계-02 Dynamic Routing (Command).

    START → collect → inspect ═(Command: 실행 중에 정한 곳들로)═▶ write_section × N ─┐
                             ╚═▶ note_no_approvals ───────────────────────────────┼▶ assemble → save → END

inspect 노드가 모은 기록을 보고 다음에 갈 노드를 스스로 정해 Command(goto=...)로 돌려준다.
미리 그려 둔 conditional edge가 아니라, 노드가 실행 중 결과를 보고 다음을 정한다.

    세 가지 분기의 차이
    - Conditional Branch(1단계 03): 루프 안에서 LLM 출력(어떤 도구를 불렀나)으로 갈라진다
    - Routing(2단계 02): 일을 시작하기 전에 입력(요청 문장)을 분류한다
    - Dynamic Routing(여기): 실행 중에 나온 중간 결과(모은 기록)를 보고 노드가 다음 노드를 정한다

    python s4_graph/02_dynamic_routing.py [--month 2026-10] [--month 2026-11]
"""

from __future__ import annotations

import argparse
import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send
from typing_extensions import TypedDict

from common import graph_shape, gw_tools, monthly_report, sections
from common.runlog import RunLog

INCIDENT = ("장애", "OOM", "out of memory", "실패", "지연", "느립니다", "반려")
PLAN = ("다음", "계획", "예정", "견적", "검토")


class State(TypedDict, total=False):
    month: str
    digest: dict
    route: list[str]
    sections: Annotated[list[dict], operator.add]
    report: list[dict]
    saved_path: str


class SectionTask(TypedDict):
    idx: int
    heading: str
    month: str
    items: list[dict]
    guide: str


def title(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월 월간 업무 보고 (이재휘)"


def build_graph(llm):
    def collect(state: State):
        return {"digest": gw_tools.digest(state["month"])}

    def inspect(state: State) -> Command:
        d = state["digest"]
        mails_events = sections.items_of(d, ["mails", "events"])
        approvals = sections.items_of(d, ["approvals"])
        incidents = [i for i in mails_events + approvals if any(w in i["text"] for w in INCIDENT)]
        plans = [i for i in mails_events if any(w in i["text"] for w in PLAN)]
        tasks = [("주요 업무", mails_events, "")]
        if incidents:
            tasks.append(("장애와 대응", incidents, "장애·반려처럼 문제가 생긴 일과 대응만 쓴다."))
        if approvals:
            tasks.append(("결재 진행", approvals, "결재 문서와 결과(승인·반려 사유)를 쓴다."))
        if plans:
            tasks.append(("다음 달 계획", plans, "앞으로 할 일·검토 중인 일만 쓴다."))
        goto = [Send("write_section", {"idx": i, "heading": h, "month": state["month"], "items": it, "guide": g})
                for i, (h, it, g) in enumerate(tasks)]
        route = [h for h, _, _ in tasks]
        if not approvals:
            goto.append(Send("note_no_approvals", {"idx": len(tasks), "heading": "결재 진행", "month": state["month"],
                                                   "items": [], "guide": ""}))
            route.append("결재 진행(기록 없음 안내)")
        return Command(update={"route": route}, goto=goto)

    def write_section(t: SectionTask):
        body = sections.write_section(llm, title(t["month"]), t["heading"], t["items"], guide=t["guide"])
        return {"sections": [{"idx": t["idx"], "heading": t["heading"], "body": body}]}

    def note_no_approvals(t: SectionTask):
        # LLM을 부르지 않는다. 없는 기록에 대해 쓸 것은 "없다"는 사실뿐이다.
        return {"sections": [{"idx": t["idx"], "heading": t["heading"],
                              "body": f"{t['month']} 기간에 기안하거나 결재선에 오른 결재 문서가 없다."}]}

    def assemble(state: State):
        return {"report": sorted(state["sections"], key=lambda s: s["idx"])}

    def save(state: State):
        path = monthly_report.save(title(state["month"]), state["report"], stem=f"s4_02_dynamic_{state['month']}")
        return {"saved_path": str(path)}

    g = StateGraph(State)
    g.add_node("collect", collect)
    g.add_node("inspect", inspect, destinations=("write_section", "note_no_approvals"))
    g.add_node("write_section", write_section)
    g.add_node("note_no_approvals", note_no_approvals)
    g.add_node("assemble", assemble)
    g.add_node("save", save)
    g.add_edge(START, "collect")
    g.add_edge("collect", "inspect")
    g.add_edge(["write_section"], "assemble")
    g.add_edge("note_no_approvals", "assemble")
    g.add_edge("assemble", "save")
    g.add_edge("save", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", action="append")
    args = ap.parse_args(argv)
    msdf.connect()
    graph = build_graph(get_llm())
    for month in args.month or ["2026-10", "2026-11"]:
        log = RunLog("s4_02_dynamic_routing", meta={"month": month})
        log.write("shape", {"kind": graph_shape.kind(graph), "mermaid": graph_shape.mermaid(graph)})
        state = log.stream(graph, {"month": month})
        known = {i["ref"] for i in state["digest"]["items"]}
        log.write("end", {"month": month, "counts": state["digest"]["counts"], "route": state["route"],
                          "problems": {s["heading"]: monthly_report.check_refs(s["body"], known)
                                       for s in state["report"] if "기록 없음" not in s["body"] and "결재 문서가 없다" not in s["body"]},
                          "saved_path": state["saved_path"]})


if __name__ == "__main__":
    main(sys.argv[1:])
