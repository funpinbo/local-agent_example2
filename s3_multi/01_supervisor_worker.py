"""3단계-01 Supervisor · Worker.

              ┌▶ mail_agent ─────┐
    START → supervisor ─┼▶ schedule_agent ─┼▶ supervisor … ─▶ writer → END
              └▶ approval_agent ─┘

supervisor LLM이 매 차례 다음에 누가 일할지와 시킬 일을 정한다.
일을 받는 쪽은 각자 자기 도구만 가진 Agent(작은 ReAct 루프)다. 2단계 Orchestrator–Worker는 일을 받는 쪽이 LLM 호출 한 번이었다.
각 Agent의 결과물은 정리한 메모(글)이고, supervisor는 메모의 길이·근거 수만 보고 다음을 정한다.

    python s3_multi/01_supervisor_worker.py
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
MAX_TURNS = 8
WORKERS = {
    "mail_agent": (gw_tools.MAIL_TOOLS, "메일 담당"),
    "schedule_agent": (gw_tools.EVENT_TOOLS, "일정 담당"),
    "approval_agent": (gw_tools.APPROVAL_TOOLS, "결재 담당"),
}
WORKER_SYSTEM = ("너는 {role}이다. 도구를 한 번에 하나씩 써서 그룹웨어 기록을 확인한다. "
                 "받은 일에 맞게 확인한 내용을 항목별로 정리해 답한다. 항목마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다. "
                 "기록에 없는 내용은 쓰지 않는다.")


class Decision(BaseModel):
    next: Literal["mail_agent", "schedule_agent", "approval_agent", "writer", "FINISH"] = Field(
        description="다음에 일할 담당. 자료가 모였으면 writer")
    task: str = Field(description="그 담당에게 시킬 일 한두 문장")


Report = monthly_report.Report


def merge(a, b):
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    request: str
    notes: Annotated[dict, merge]          # 담당 → 메모
    seen: Annotated[dict, merge]           # 담당 → 실제로 본 근거 id
    decisions: Annotated[list[dict], operator.add]
    runs: Annotated[list[dict], operator.add]   # Agent 실행 기록
    task: str
    next: str
    title: str
    sections: list[dict]
    problems: list[str]
    saved_path: str
    outcome: str


def build_graph(llm):
    agents = {name: react_agent(llm, tools, WORKER_SYSTEM.format(role=role)) for name, (tools, role) in WORKERS.items()}

    def supervisor(state: State):
        status = "\n".join(f"- {n}: 메모 {len(state.get('notes', {}).get(n, ''))}자, 근거 {len(state.get('seen', {}).get(n, []))}개"
                           if n in state.get("notes", {}) else f"- {n}: 아직 일하지 않음" for n in WORKERS)
        d = Decision.model_validate(llm.with_structured_output(Decision).invoke([
            SystemMessage("너는 월간 보고 작성 팀의 관리자다. 담당은 mail_agent(메일), schedule_agent(일정), "
                          "approval_agent(결재), writer(보고서 작성)다. 매 차례 다음 담당과 시킬 일을 정한다."),
            HumanMessage(f"[요청]\n{state['request']}\n\n[지금까지]\n{status}")]))
        turn = len(state.get("decisions", [])) + 1
        nxt = d.next if turn <= MAX_TURNS else "writer"      # 안전 종료: 차례가 넘치면 작성으로
        return {"decisions": [{"turn": turn, "next": d.next, "task": d.task, "forced": nxt != d.next}],
                "next": nxt, "task": d.task}

    def make_worker(name: str):
        def work(state: State):
            r = run_agent(agents[name], f"{state['task']}\n(대상 기간: {MONTH})")
            return {"notes": {name: r["note"]}, "seen": {name: r["seen"]},
                    "runs": [{"agent": name, "task": state["task"], "tools": r["tools"], "steps": r["steps"],
                              "note_chars": len(r["note"]), "seen": len(r["seen"])}]}
        return work

    def writer(state: State):
        notes = "\n\n".join(f"[{n}]\n{t}" for n, t in state.get("notes", {}).items())
        rep = Report.model_validate(llm.with_structured_output(Report).invoke([HumanMessage(
            f"[요청]\n{state['request']}\n\n아래 담당별 메모만 근거로 월간 보고를 쓴다. 섹션은 주요 업무, 장애와 대응, 결재 진행, 다음 달 계획. "
            f"문장마다 메모에 있는 근거 id를 [mail:12]처럼 그대로 단다.\n\n{notes or '(메모 없음)'}")]))
        sections = [s.model_dump() for s in rep.sections]
        known = {r for ids in state.get("seen", {}).values() for r in ids}
        problems = [f"{s['heading']}: {p}" for s in sections for p in monthly_report.check_refs(s["body"], known)]
        path = monthly_report.save(rep.title, sections, stem="s3_01_supervisor")
        return {"title": rep.title, "sections": sections, "problems": problems, "saved_path": str(path),
                "outcome": "saved"}

    def route(state: State) -> str:
        return END if state["next"] == "FINISH" else state["next"]

    g = StateGraph(State)
    g.add_node("supervisor", supervisor)
    for name in WORKERS:
        g.add_node(name, make_worker(name))
        g.add_edge(name, "supervisor")
    g.add_node("writer", writer)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route, [*WORKERS, "writer", END])
    g.add_edge("writer", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    log = RunLog("s3_01_supervisor_worker", meta={"request": REQUEST, "month": MONTH})
    state = log.stream(build_graph(get_llm()), {"request": REQUEST}, {"recursion_limit": 60})
    log.write("end", {k: state.get(k) for k in ("outcome", "decisions", "runs", "problems", "saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
