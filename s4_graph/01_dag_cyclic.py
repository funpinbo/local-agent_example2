"""4단계-01 DAG와 Cyclic 그래프.

    DAG:     START → collect → write → review → save → END                    (되돌아가는 edge 없음, 한 번 지나감)
    Cyclic:  START → collect → write → review ─(문제 있음)─▶ revise → review …  (종료 조건이 있어야 끝난다)
                                               └─(끝)──▶ save → END

같은 월간 보고를 두 모양으로 만들어 비교한다. Cyclic의 종료 조건은 세 가지다.
  1) 문제가 없다   2) 고친 횟수가 MAX_ROUNDS   3) 고쳐도 문제 수가 줄지 않는다
그래프 모양은 코드로 확인한다(common/graph_shape: 순환 찾기, Mermaid).

    python s4_graph/01_dag_cyclic.py [--month 2026-10]
"""

from __future__ import annotations

import argparse
import operator
import sys
import time
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from common import graph_shape, gw_tools, monthly_report, sections
from common.runlog import RunLog

MAX_ROUNDS = 2
SOURCES = {"주요 업무": ["mails", "events"], "장애와 대응": ["mails"], "결재 진행": ["approvals"],
           "다음 달 계획": ["mails", "events"]}


class State(TypedDict, total=False):
    month: str
    digest: dict
    sections: list[dict]
    problems: dict                 # 섹션 → 문제 목록
    rounds: Annotated[list[dict], operator.add]
    stop_reason: str
    saved_path: str


def title(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월 월간 업무 보고 (이재휘)"


def build_graph(llm, *, cyclic: bool):
    def collect(state: State):
        return {"digest": gw_tools.digest(state["month"])}

    def write(state: State):
        done: list[dict] = []
        for h in sections.SECTIONS:
            body = sections.write_section(llm, title(state["month"]), h,
                                          sections.items_of(state["digest"], SOURCES[h]), done)
            done.append({"heading": h, "body": body})
        return {"sections": done}

    def review(state: State):
        known = {i["ref"] for i in state["digest"]["items"]}
        probs = {s["heading"]: monthly_report.check_refs(s["body"], known) for s in state["sections"]}
        probs = {h: p for h, p in probs.items() if p}
        return {"problems": probs, "rounds": [{"round": len(state.get("rounds", [])) + 1,
                                               "problems": sum(len(p) for p in probs.values()),
                                               "sections": list(probs)}]}

    def decide(state: State) -> str:
        """Cyclic의 종료 조건. 셋 중 하나면 끝낸다."""
        r = state["rounds"]
        if not state["problems"]:
            return "save"
        if len(r) > MAX_ROUNDS:
            return "save"
        if len(r) >= 2 and r[-1]["problems"] >= r[-2]["problems"]:
            return "save"
        return "revise"

    def revise(state: State):
        # 문제가 있는 섹션만 다시 쓴다
        known = {i["ref"] for i in state["digest"]["items"]}
        out = []
        for s in state["sections"]:
            p = state["problems"].get(s["heading"])
            if p:
                body = sections.write_section(llm, title(state["month"]), s["heading"],
                                              sections.items_of(state["digest"], SOURCES[s["heading"]]), out,
                                              feedback="\n".join(p) + f"\n가능한 id 예: {', '.join(sorted(known)[:5])}")
                out.append({"heading": s["heading"], "body": body})
            else:
                out.append(s)
        return {"sections": out}

    def save(state: State):
        r = state.get("rounds", [])
        if not state["problems"]:
            why = "문제 없음"
        elif not cyclic:
            why = "DAG: 한 번 지나감"
        elif len(r) > MAX_ROUNDS:
            why = f"고친 횟수 {MAX_ROUNDS}번"
        else:
            why = "고쳐도 문제 수가 줄지 않음"
        path = monthly_report.save(title(state["month"]), state["sections"], stem=f"s4_01_{'cyclic' if cyclic else 'dag'}")
        return {"stop_reason": why, "saved_path": str(path)}

    g = StateGraph(State)
    for n, fn in [("collect", collect), ("write", write), ("review", review), ("save", save)]:
        g.add_node(n, fn)
    g.add_edge(START, "collect")
    g.add_edge("collect", "write")
    g.add_edge("write", "review")
    if cyclic:
        g.add_node("revise", revise)
        g.add_conditional_edges("review", decide, ["revise", "save"])
        g.add_edge("revise", "review")
    else:
        g.add_edge("review", "save")
    g.add_edge("save", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-10")
    args = ap.parse_args(argv)
    msdf.connect()
    llm = get_llm()
    for cyclic in (False, True):
        graph = build_graph(llm, cyclic=cyclic)
        shape = {"kind": graph_shape.kind(graph), "cycles": graph_shape.cycles(graph), "mermaid": graph_shape.mermaid(graph)}
        log = RunLog("s4_01_dag_cyclic", meta={"month": args.month, "cyclic": cyclic, "shape": shape["kind"]})
        log.write("shape", shape)
        t0 = time.perf_counter()
        state = log.stream(graph, {"month": args.month})
        log.write("end", {"shape": shape["kind"], "sec": round(time.perf_counter() - t0, 2), "rounds": state.get("rounds"),
                          "stop_reason": state.get("stop_reason"), "saved_path": state.get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
