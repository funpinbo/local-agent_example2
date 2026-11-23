"""4단계-03 Planner–Executor · Task Decomposition · Replanning.

    START → plan → check_plan ─▶ execute ─┬▶ execute (다음 단계)
                  ▲                       ├▶ replan ─▶ check_plan    (실행 결과가 계획의 전제와 다르면)
                  └── (계획이 틀림, 1번) ──┘└▶ save → END

- Planner(LLM): 요청을 실행할 단계 목록으로 나눈다(Task Decomposition). 쓸 수 있는 동작은 정해져 있다.
- check_plan(코드): 계획 관문. 없는 동작, 모으지 않은 자료로 쓰는 섹션, 빠진 finish를 거른다.
- Executor(코드): 단계를 하나씩 실행한다. 수집은 MSDF, 섹션 작성만 LLM.
- Replanning: 수집한 자료가 0건인데 뒤 단계가 그 자료로 섹션을 쓰게 되어 있으면 Planner에게 다시 맡긴다.
  (11월에는 결재 기록이 없다)

2단계 Orchestrator–Worker는 그때그때 나눠 맡겼다. 여기서는 계획을 먼저 세우고 실행하며, 실행 결과로 계획을 고친다.

    python s4_graph/03_planner_executor.py [--month 2026-11] [--month 2026-10]
"""

from __future__ import annotations

import argparse
import operator
import sys
from pathlib import Path
from typing import Annotated, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import gw_tools, monthly_report, sections
from common.runlog import RunLog

MAX_REPLANS, MAX_STEPS = 2, 15
ACTIONS = ("collect_mails", "collect_events", "collect_approvals", "write_section", "finish")
KIND_OF = {"collect_mails": "mails", "collect_events": "events", "collect_approvals": "approvals"}


def request_for(month: str) -> str:
    y, m = month.split("-")
    return f"이재휘의 {y}년 {int(m)}월 월간 업무 보고를 그룹웨어 기록으로 작성해 줘. 주요 업무, 결재 진행 상황, 다음 달 계획을 꼭 넣어 줘."


class Step(BaseModel):
    action: Literal["collect_mails", "collect_events", "collect_approvals", "write_section", "finish"]
    heading: str = Field(default="", description="write_section일 때 섹션 제목")
    sources: list[Literal["mails", "events", "approvals"]] = Field(default_factory=list,
                                                                    description="write_section일 때 근거로 쓸 자료 종류")


class Plan(BaseModel):
    steps: list[Step]


class State(TypedDict, total=False):
    month: str
    request: str
    plan: list[dict]
    cursor: int
    data: dict                     # 종류 → 항목 목록
    sections: list[dict]
    observations: Annotated[list[str], operator.add]
    plans: Annotated[list[dict], operator.add]     # 세운 계획 기록
    executed: Annotated[list[dict], operator.add]
    replans: int
    replan_tries: int
    plan_before: list[dict]        # 다시 세우기 전 계획 (새 계획이 계속 틀리면 이것으로 돌아간다)
    plan_problems: list[str]
    plan_tries: int
    need: str                      # execute 다음: next | replan | save
    saved_path: str
    outcome: str


PLANNER_SYSTEM = (
    "너는 월간 보고 작성 계획을 세운다. 쓸 수 있는 동작: collect_mails, collect_events, collect_approvals(그 달 기록 수집), "
    "write_section(heading, sources: 모은 자료 중 근거로 쓸 종류), finish(마지막). "
    "섹션은 그 섹션에 맞는 자료를 먼저 모은 뒤에 쓴다. 마지막은 finish다.")


def plan_problems(steps: list[dict]) -> list[str]:
    p, collected = [], set()
    for i, s in enumerate(steps):
        if s["action"] in KIND_OF:
            collected.add(KIND_OF[s["action"]])
        elif s["action"] == "write_section":
            if not s.get("heading"):
                p.append(f"{i + 1}번 write_section에 제목이 없습니다.")
            missing = set(s.get("sources") or []) - collected
            if not s.get("sources"):
                p.append(f"{i + 1}번 '{s.get('heading')}'에 근거 자료가 없습니다.")
            elif missing:
                p.append(f"{i + 1}번 '{s.get('heading')}'이 아직 모으지 않은 자료({', '.join(sorted(missing))})를 씁니다.")
    if not steps or steps[-1]["action"] != "finish":
        p.append("마지막 단계가 finish가 아닙니다.")
    if len(steps) > MAX_STEPS:
        p.append(f"단계가 너무 많습니다({len(steps)}개).")
    return p


def build_graph(llm):
    def ask_planner(state: State, extra: str) -> list[dict]:
        out = Plan.model_validate(llm.with_structured_output(Plan).invoke([
            SystemMessage(PLANNER_SYSTEM), HumanMessage(f"[요청]\n{state['request']}\n\n{extra}")]))
        return [s.model_dump() for s in out.steps]

    def plan(state: State):
        extra = f"[계획 관문에서 걸린 점 — 고쳐서 다시]\n" + "\n".join(state["plan_problems"]) if state.get("plan_problems") else "단계 목록을 세운다."
        steps = ask_planner(state, extra)
        return {"plan": steps, "cursor": 0, "plan_tries": state.get("plan_tries", 0) + 1,
                "plans": [{"kind": "plan", "steps": steps}]}

    def check_plan(state: State):
        return {"plan_problems": plan_problems(state["plan"])}

    def after_check(state: State) -> str:
        if not state["plan_problems"]:
            return "execute"
        return "plan" if state["plan_tries"] < 2 else "give_up"

    def execute(state: State):
        step = state["plan"][state["cursor"]]
        data, secs = dict(state.get("data", {})), list(state.get("sections", []))
        rec = {"n": len(state.get("executed", [])) + 1, **step}
        obs: list[str] = []
        need = "next"
        if step["action"] in KIND_OF:
            kind = KIND_OF[step["action"]]
            items = sections.items_of(gw_tools.digest(state["month"]), [kind])
            data[kind] = items
            rec["result"] = f"{len(items)}건"
            if not items:
                later = [s for s in state["plan"][state["cursor"] + 1:] if kind in (s.get("sources") or [])]
                if later:
                    obs.append(f"{state['month']} {sections.KIND[kind]} 기록이 0건이다. 남은 계획의 "
                               f"{', '.join(repr(s['heading']) for s in later)}는 이 자료를 근거로 하게 되어 있다.")
                    need = "replan"
        elif step["action"] == "write_section":
            items = [i for k in step["sources"] for i in data.get(k, [])]
            body = sections.write_section(llm, f"{state['month']} 월간 업무 보고", step["heading"], items, secs)
            secs.append({"heading": step["heading"], "body": body, "sources": step["sources"]})
            rec["result"] = f"{len(body)}자, 근거 {len(monthly_report.refs(body))}개"
        else:   # finish
            need = "save"
        if need == "next" and state["cursor"] + 1 >= len(state["plan"]):
            need = "save"
        if len(state.get("executed", [])) + 1 >= MAX_STEPS:
            need = "save"
        return {"data": data, "sections": secs, "executed": [rec], "observations": obs, "need": need,
                "cursor": state["cursor"] + 1}

    def after_execute(state: State) -> str:
        if state["need"] == "replan":
            if state.get("replans", 0) < MAX_REPLANS:
                return "replan"
            return "execute" if state["cursor"] < len(state["plan"]) else "save"
        return "save" if state["need"] == "save" else "execute"

    def replan(state: State):
        done = "\n".join(f"- {e['action']} {e.get('heading', '')}: {e.get('result', '')}" for e in state["executed"])
        retry = state.get("replan_tries", 0) > 0
        before = state["plan_before"] if retry else state["plan"]
        left = before[state["cursor"]:]
        extra = (f"[이미 한 단계]\n{done}\n\n[관찰]\n" + "\n".join(state["observations"]) +
                 f"\n\n[남은 계획]\n{left}\n\n남은 계획을 관찰에 맞게 다시 세운다. 이미 모은 자료는 다시 모으지 않아도 된다. "
                 f"이미 모은 자료: {', '.join(state['data']) or '없음'}.")
        if retry:
            extra += "\n\n[계획 관문에서 걸린 점 — 고쳐서 다시]\n" + "\n".join(state["plan_problems"])
        steps = ask_planner(state, extra)
        # 이미 실행한 단계는 그대로 두고, 남은 부분만 새 계획으로 바꾼다
        new_plan = before[:state["cursor"]] + steps
        return {"plan": new_plan, "plan_before": before, "replan_tries": state.get("replan_tries", 0) + 1,
                "replans": state.get("replans", 0) + (0 if retry else 1), "plans": [{"kind": "replan", "steps": steps}]}

    def check_replan(state: State):
        # 이미 모은 자료는 수집 단계를 지나간 것으로 보고 검사한다
        pre = [{"action": a} for a, k in KIND_OF.items() if k in state.get("data", {})]
        return {"plan_problems": plan_problems(pre + state["plan"][state["cursor"]:])}

    def after_check_replan(state: State) -> str:
        # 새 계획이 관문을 통과하면 실행, 아니면 한 번 더 세우고, 그래도 안 되면 원래 계획대로 간다.
        # (처음엔 실패하면 바로 저장해서, 섹션이 하나도 없는 보고서가 저장됐다)
        if not state["plan_problems"]:
            return "accept_replan"
        return "replan" if state["replan_tries"] < 2 else "keep_plan"

    def accept_replan(state: State):
        return {"replan_tries": 0}

    def keep_plan(state: State):
        return {"plan": state["plan_before"], "replan_tries": 0,
                "observations": ["새 계획이 관문을 두 번 통과하지 못해 원래 계획대로 진행한다."]}

    def save(state: State):
        known = {i["ref"] for items in state.get("data", {}).values() for i in items}
        secs = [{"heading": s["heading"], "body": s["body"]} for s in state.get("sections", [])]
        problems = {s["heading"]: monthly_report.check_refs(s["body"], known) for s in secs}
        path = monthly_report.save(f"{state['month']} 월간 업무 보고 (이재휘)", secs, stem=f"s4_03_planner_{state['month']}")
        return {"saved_path": str(path), "outcome": "saved",
                "observations": [f"근거 검사: {sum(1 for p in problems.values() if p)}개 섹션 문제"]}

    def give_up(state: State):
        return {"outcome": "plan_rejected"}

    g = StateGraph(State)
    for n, fn in [("plan", plan), ("check_plan", check_plan), ("execute", execute), ("replan", replan),
                  ("check_replan", check_replan), ("accept_replan", accept_replan), ("keep_plan", keep_plan),
                  ("save", save), ("give_up", give_up)]:
        g.add_node(n, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "check_plan")
    g.add_conditional_edges("check_plan", after_check, ["execute", "plan", "give_up"])
    g.add_conditional_edges("execute", after_execute, ["execute", "replan", "save"])
    g.add_edge("replan", "check_replan")
    g.add_conditional_edges("check_replan", after_check_replan, ["accept_replan", "replan", "keep_plan"])
    g.add_edge("accept_replan", "execute")
    g.add_edge("keep_plan", "execute")
    g.add_edge("save", END)
    g.add_edge("give_up", END)
    return g.compile()


def inputs(month: str) -> dict:
    return {"month": month, "request": request_for(month), "data": {}, "sections": [], "replans": 0}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", action="append")
    args = ap.parse_args(argv)
    msdf.connect()
    graph = build_graph(get_llm())
    for month in args.month or ["2026-11", "2026-10"]:
        log = RunLog("s4_03_planner_executor", meta={"month": month, "request": request_for(month)})
        state = log.stream(graph, inputs(month), {"recursion_limit": 80})
        log.write("end", {k: state.get(k) for k in ("outcome", "plans", "executed", "observations", "replans",
                                                     "plan_problems", "saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
