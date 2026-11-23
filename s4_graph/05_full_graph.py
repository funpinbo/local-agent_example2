"""4단계-05 전체 그래프 한 장.

    START → planner_executor(Subgraph: 03) → review ─┬▶ revise ─▶ review     (Cyclic, 종료 조건 3개: 01)
                                                     └▶ save_docx → prepare → human ─┬▶ submit → END     (HITL: 04)
                                                                          ▲          ├▶ apply_edit ─┘
                                                                          └──────────┘└▶ rejected → END

1~4에서 만든 것을 한 그래프로 묶는다.
- 계획·실행·재계획은 03 그래프를 Subgraph로 그대로 쓴다(안의 State는 밖으로 나오지 않고, 섹션·자료만 올라온다).
- 근거 검사와 고쳐 쓰기는 01의 Cyclic 종료 조건을 쓴다.
- 결재 상신은 04처럼 사람의 approve 뒤에만 한다.
그래프 그림(Mermaid)은 blog/s4_full_graph.mmd에 저장한다.

    python s4_graph/05_full_graph.py [--month 2026-11] [--auto approve|reject]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import operator
import sys
import uuid
from pathlib import Path
from typing import Annotated

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from common import approval, config, graph_shape, monthly_report, sections
from common.runlog import RunLog

MAX_ROUNDS = 2


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"s4_{name}", ROOT / "s4_graph" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


planner = _load("03_planner_executor")


class State(TypedDict, total=False):
    month: str
    data: dict
    sections: list[dict]
    pe: dict                       # 계획·실행 요약 (Subgraph가 올린 것)
    problems: dict
    rounds: Annotated[list[dict], operator.add]
    docx_path: str
    draft: dict
    decisions: Annotated[list[dict], operator.add]
    submitted: dict
    outcome: str


def title(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월 월간 업무 보고 (이재휘)"


def build_graph(llm, checkpointer=None):
    pe_graph = planner.build_graph(llm)

    def planner_executor(state: State):
        out = pe_graph.invoke(planner.inputs(state["month"]), {"recursion_limit": 80})
        return {"data": out.get("data", {}), "sections": [{"heading": s["heading"], "body": s["body"],
                                                            "sources": s.get("sources", [])} for s in out.get("sections", [])],
                "pe": {"plans": out.get("plans"), "executed": out.get("executed"), "replans": out.get("replans"),
                       "observations": out.get("observations"), "outcome": out.get("outcome")}}

    def known(state: State) -> set[str]:
        return {i["ref"] for items in state.get("data", {}).values() for i in items}

    def review(state: State):
        probs = {s["heading"]: monthly_report.check_refs(s["body"], known(state)) for s in state["sections"]}
        probs = {h: p for h, p in probs.items() if p}
        return {"problems": probs, "rounds": [{"round": len(state.get("rounds", [])) + 1,
                                               "problems": sum(len(p) for p in probs.values()), "sections": list(probs)}]}

    def decide(state: State) -> str:
        r = state["rounds"]
        if not state["problems"] or len(r) > MAX_ROUNDS or (len(r) >= 2 and r[-1]["problems"] >= r[-2]["problems"]):
            return "save_docx"
        return "revise"

    def revise(state: State):
        out = []
        for s in state["sections"]:
            p = state["problems"].get(s["heading"])
            if p:
                items = [i for k in s.get("sources", []) for i in state["data"].get(k, [])]
                s = {**s, "body": sections.write_section(llm, title(state["month"]), s["heading"], items, out,
                                                         feedback="\n".join(p))}
            out.append(s)
        return {"sections": out}

    def save_docx(state: State):
        secs = [{"heading": s["heading"], "body": s["body"]} for s in state["sections"]]
        return {"docx_path": str(monthly_report.save(title(state["month"]), secs, stem=f"s4_05_full_{state['month']}"))}

    def prepare(state: State):
        summary = " / ".join(f"{s['heading']}: {s['body'][:60]}…" for s in state["sections"])
        return {"draft": {"kind": "월간보고", "title": title(state["month"]), "line": approval.LINE,
                          "body": f"{state['month']} 월간 업무 보고를 첨부합니다. 요약: {summary}",
                          "attachment": Path(state["docx_path"]).name,
                          "replans": state["pe"].get("replans"), "unresolved": state.get("problems", {})}}

    def human(state: State):
        answer = interrupt({"question": "이 문서로 결재를 상신할까요? (approve / edit / reject)", "draft": state["draft"]})
        return {"decisions": [answer if isinstance(answer, dict) else {"action": str(answer)}]}

    def route(state: State) -> str:
        return {"approve": "submit", "edit": "apply_edit"}.get(state["decisions"][-1].get("action"), "rejected")

    def apply_edit(state: State):
        ch = state["decisions"][-1].get("changes", {})
        return {"draft": {**state["draft"], **{k: v for k, v in ch.items() if k in ("title", "line", "body")}}}

    def submit(state: State):
        return {"submitted": approval.submit(state["draft"], state["docx_path"]), "outcome": "submitted"}

    def rejected(state: State):
        return {"outcome": "not_submitted"}

    g = StateGraph(State)
    for n, fn in [("planner_executor", planner_executor), ("review", review), ("revise", revise),
                  ("save_docx", save_docx), ("prepare", prepare), ("human", human), ("apply_edit", apply_edit),
                  ("submit", submit), ("rejected", rejected)]:
        g.add_node(n, fn)
    g.add_edge(START, "planner_executor")
    g.add_edge("planner_executor", "review")
    g.add_conditional_edges("review", decide, ["revise", "save_docx"])
    g.add_edge("revise", "review")
    g.add_edge("save_docx", "prepare")
    g.add_edge("prepare", "human")
    g.add_conditional_edges("human", route, ["submit", "apply_edit", "rejected"])
    g.add_edge("apply_edit", "human")
    g.add_edge("submit", END)
    g.add_edge("rejected", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def run(graph, month: str, decide, log: RunLog) -> dict:
    cfg = {"configurable": {"thread_id": f"full-{uuid.uuid4().hex[:8]}"}, "recursion_limit": 60}
    log.stream(graph, {"month": month}, cfg)
    while (snap := graph.get_state(cfg)).next:
        payload = snap.tasks[0].interrupts[0].value
        log.write("interrupt", {"next": snap.next, "draft": payload["draft"]})
        answer = decide(payload)
        log.write("human", answer)
        log.stream(graph, Command(resume=answer), cfg)
    return graph.get_state(cfg).values


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-11")
    ap.add_argument("--auto", choices=["approve", "reject"])
    args = ap.parse_args(argv)
    msdf.connect()
    graph = build_graph(get_llm())
    mmd = graph_shape.mermaid(graph)
    (config.ROOT / "blog" / "s4_full_graph.mmd").write_text(mmd, encoding="utf-8")
    hitl = _load("04_human_in_the_loop")
    decide = (lambda p: {"action": args.auto}) if args.auto else hitl.ask_terminal
    log = RunLog("s4_05_full_graph", meta={"month": args.month, "auto": args.auto, "shape": graph_shape.kind(graph)})
    state = run(graph, args.month, decide, log)
    log.write("end", {"outcome": state.get("outcome"), "pe": state.get("pe"), "rounds": state.get("rounds"),
                      "unresolved": state.get("problems"), "submitted": state.get("submitted"),
                      "docx_path": state.get("docx_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
