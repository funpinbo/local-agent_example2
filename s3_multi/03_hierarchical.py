"""3단계-03 Hierarchical (팀을 Subgraph로).

    START → top ─┬▶ collect_team ─▶ top          collect_team(Subgraph):  lead ⇄ {mail_agent, schedule_agent, approval_agent}
                 ├▶ write_team   ─▶ top          write_team(Subgraph):    drafter → reviewer ─(문제)▶ drafter → saver
                 └▶ END

위의 관리자(top)는 팀 단위로만 일을 맡긴다. 팀 안에서는 팀장(lead)이 다시 담당 Agent에게 나눠 준다.
팀마다 자기 State를 갖는다. 팀 안의 Agent 대화·도구 결과는 팀 안에 두고, 위로는 메모와 근거 id만 올린다
(2편 2(2)의 Subgraph: 검색 원본은 안에, 검증된 핵심만 바깥 State로).

    python s3_multi/03_hierarchical.py
"""

from __future__ import annotations

import operator
import sys
from pathlib import Path
from typing import Annotated, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import gw_tools, monthly_report
from common.agents import react_agent, run_agent
from common.runlog import RunLog

MONTH = "2026-10"
REQUEST = "이재휘의 2026년 10월 월간 업무 보고를 그룹웨어 기록(메일·일정·결재)을 근거로 작성해 줘."
MAX_TOP_TURNS, MAX_LEAD_TURNS = 5, 6
WORKERS = {"mail_agent": (gw_tools.MAIL_TOOLS, "메일 담당"),
           "schedule_agent": (gw_tools.EVENT_TOOLS, "일정 담당"),
           "approval_agent": (gw_tools.APPROVAL_TOOLS, "결재 담당")}
WORKER_SYSTEM = ("너는 {role}이다. 도구를 한 번에 하나씩 써서 그룹웨어 기록을 확인한다. 받은 일에 맞게 확인한 내용을 항목별로 정리해 답한다. "
                 "항목마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다. 기록에 없는 내용은 쓰지 않는다.")


def merge(a, b):
    return {**(a or {}), **(b or {})}


# ---------------- 수집 팀 ----------------
class LeadDecision(BaseModel):
    next: Literal["mail_agent", "schedule_agent", "approval_agent", "DONE"] = Field(description="다음 담당, 다 모았으면 DONE")
    task: str = Field(description="시킬 일")


class CollectState(TypedDict, total=False):
    goal: str
    notes: Annotated[dict, merge]
    seen: Annotated[dict, merge]
    turns: Annotated[list[dict], operator.add]
    next: str
    task: str


def collect_team(llm):
    agents = {n: react_agent(llm, t, WORKER_SYSTEM.format(role=r)) for n, (t, r) in WORKERS.items()}

    def lead(state: CollectState):
        status = "\n".join(f"- {n}: {'메모 %d자' % len(state['notes'][n]) if n in state.get('notes', {}) else '아직'}" for n in WORKERS)
        d = LeadDecision.model_validate(llm.with_structured_output(LeadDecision).invoke([
            SystemMessage("너는 그룹웨어 기록 수집 팀장이다. 담당은 mail_agent, schedule_agent, approval_agent다."),
            HumanMessage(f"[팀 목표]\n{state['goal']}\n\n[지금까지]\n{status}")]))
        n = len(state.get("turns", [])) + 1
        nxt = d.next if n <= MAX_LEAD_TURNS else "DONE"
        return {"turns": [{"turn": n, "next": d.next, "task": d.task}], "next": nxt, "task": d.task}

    def make(name):
        def work(state: CollectState):
            r = run_agent(agents[name], f"{state['task']}\n(대상 기간: {MONTH})")
            return {"notes": {name: r["note"]}, "seen": {name: r["seen"]}}
        return work

    g = StateGraph(CollectState)
    g.add_node("lead", lead)
    for n in WORKERS:
        g.add_node(n, make(n))
        g.add_edge(n, "lead")
    g.add_edge(START, "lead")
    g.add_conditional_edges("lead", lambda s: END if s["next"] == "DONE" else s["next"], [*WORKERS, END])
    return g.compile()


# ---------------- 작성 팀 ----------------
class WriteState(TypedDict, total=False):
    request: str
    notes: dict
    known: list[str]
    title: str
    sections: list[dict]
    problems: list[str]
    rounds: int
    saved_path: str


def write_team(llm):
    def drafter(state: WriteState):
        notes = "\n\n".join(f"[{n}]\n{t}" for n, t in state["notes"].items())
        ask = (f"[요청]\n{state['request']}\n\n아래 메모만 근거로 월간 보고를 쓴다. 섹션은 주요 업무, 장애와 대응, 결재 진행, 다음 달 계획. "
               f"문장마다 메모의 근거 id를 [mail:12]처럼 그대로 단다.\n\n{notes or '(메모 없음)'}")
        if state.get("problems"):
            ask += "\n\n[검토 의견]\n" + "\n".join(f"- {p}" for p in state["problems"])
        rep = monthly_report.Report.model_validate(llm.with_structured_output(monthly_report.Report).invoke([HumanMessage(ask)]))
        return {"title": rep.title, "sections": [s.model_dump() for s in rep.sections], "rounds": state.get("rounds", 0) + 1}

    def reviewer(state: WriteState):
        known = set(state["known"])
        return {"problems": [f"{s['heading']}: {p}" for s in state["sections"] for p in monthly_report.check_refs(s["body"], known)]}

    def saver(state: WriteState):
        return {"saved_path": str(monthly_report.save(state["title"], state["sections"], stem="s3_03_hierarchical"))}

    g = StateGraph(WriteState)
    g.add_node("drafter", drafter)
    g.add_node("reviewer", reviewer)
    g.add_node("saver", saver)
    g.add_edge(START, "drafter")
    g.add_edge("drafter", "reviewer")
    g.add_conditional_edges("reviewer", lambda s: "drafter" if s["problems"] and s["rounds"] < 2 else "saver",
                            ["drafter", "saver"])
    g.add_edge("saver", END)
    return g.compile()


# ---------------- 위 관리자 ----------------
class TopDecision(BaseModel):
    next: Literal["collect_team", "write_team", "FINISH"] = Field(description="다음에 일할 팀")
    reason: str


class State(TypedDict, total=False):
    request: str
    notes: dict          # 수집 팀이 올린 메모만
    known: list[str]     # 수집 팀이 올린 근거 id만
    report: dict         # 작성 팀이 올린 결과만
    top_turns: Annotated[list[dict], operator.add]
    team_log: Annotated[list[dict], operator.add]
    next: str


def build_graph(llm):
    collect, write = collect_team(llm), write_team(llm)

    def top(state: State):
        status = (f"- 수집: {'메모 %d개, 근거 %d개' % (len(state['notes']), len(state['known'])) if state.get('notes') else '아직'}\n"
                  f"- 작성: {'저장함' if state.get('report') else '아직'}")
        d = TopDecision.model_validate(llm.with_structured_output(TopDecision).invoke([
            SystemMessage("너는 월간 보고 프로젝트 관리자다. 팀은 collect_team(그룹웨어 기록 수집)과 write_team(보고서 작성·검토·저장)이다."),
            HumanMessage(f"[요청]\n{state['request']}\n\n[진행]\n{status}")]))
        n = len(state.get("top_turns", [])) + 1
        nxt = d.next if n <= MAX_TOP_TURNS else "FINISH"
        return {"top_turns": [{"turn": n, "next": d.next, "reason": d.reason}], "next": nxt}

    def run_collect(state: State):
        out = collect.invoke({"goal": f"{state['request']} 에 필요한 메일·일정·결재 기록을 모은다."}, {"recursion_limit": 60})
        known = sorted({r for ids in out.get("seen", {}).values() for r in ids})
        return {"notes": out.get("notes", {}), "known": known,
                "team_log": [{"team": "collect", "lead_turns": out.get("turns", []),
                              "agents": {n: len(t) for n, t in out.get("notes", {}).items()}}]}

    def run_write(state: State):
        out = write.invoke({"request": state["request"], "notes": state.get("notes", {}), "known": state.get("known", [])})
        return {"report": {k: out.get(k) for k in ("title", "sections", "problems", "saved_path", "rounds")},
                "team_log": [{"team": "write", "rounds": out.get("rounds"), "problems": out.get("problems")}]}

    g = StateGraph(State)
    g.add_node("top", top)
    g.add_node("collect_team", run_collect)
    g.add_node("write_team", run_write)
    g.add_edge(START, "top")
    g.add_conditional_edges("top", lambda s: END if s["next"] == "FINISH" else s["next"], ["collect_team", "write_team", END])
    g.add_edge("collect_team", "top")
    g.add_edge("write_team", "top")
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    log = RunLog("s3_03_hierarchical", meta={"request": REQUEST})
    state = log.stream(build_graph(get_llm()), {"request": REQUEST}, {"recursion_limit": 40})
    log.write("end", {"top_turns": state.get("top_turns"), "team_log": state.get("team_log"),
                      "saved_path": (state.get("report") or {}).get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
