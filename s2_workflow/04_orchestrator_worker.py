"""2단계-04 Orchestrator–Worker.

    START → orchestrate ─(Send × N, N은 실행 중에 정해짐)─▶ worker ─▶ synthesize → save → END

오케스트레이터 LLM이 요청과 문서 목록을 보고 섹션을 몇 개로, 무엇으로 나눌지 정한다.
각 섹션은 Send로 worker에 보내져 동시에 처리되고, 결과는 reducer로 모인 뒤 synthesize가 합친다.
03 Parallelization은 섹션을 코드가 미리 정했다. 여기서는 입력에 따라 LLM이 정한다.

    python s2_workflow/04_orchestrator_worker.py ["요청 문장"]
"""

from __future__ import annotations

import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import doc_tools, tech_report
from common.runlog import RunLog

REQUEST = "로컬 LLM 서버 도입 한 달의 운영 현황을 사내 문서를 근거로 기술 보고서로 정리해 줘. 장애와 대응, 운영 정책, 앞으로의 계획을 포함해 줘."
MAX_WORKERS = 8


class Task(BaseModel):
    heading: str = Field(description="섹션 제목")
    query: str = Field(description="근거를 찾을 사내 문서 검색어")
    goal: str = Field(description="이 섹션에서 무엇을 쓸지 한 문장")


class Plan(BaseModel):
    title: str = Field(description="보고서 제목")
    tasks: list[Task] = Field(description="섹션 목록. 요청과 문서에 맞게 개수를 정한다")


def merge(a, b):
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    request: str
    title: str
    tasks: list[dict]
    sections: Annotated[list[dict], operator.add]
    chunks: Annotated[dict, merge]
    report: list[dict]
    summary: str
    saved_path: str


class WorkerInput(TypedDict):
    idx: int
    task: dict
    title: str


def build_graph(llm):
    def orchestrate(state: State):
        plan = Plan.model_validate(llm.with_structured_output(Plan).invoke([
            SystemMessage("너는 보고서 작성을 지휘한다. 요청과 문서 목록을 보고 섹션을 나누고, 섹션마다 검색어와 목표를 정한다."),
            HumanMessage(f"[요청]\n{state['request']}\n\n[사내 문서 목록]\n{doc_tools.catalog_text()}")]))
        return {"title": plan.title, "tasks": [t.model_dump() for t in plan.tasks[:MAX_WORKERS]]}

    def assign(state: State):
        # 섹션 수만큼 worker를 실행 중에 만든다
        return [Send("worker", {"idx": i, "task": t, "title": state["title"]}) for i, t in enumerate(state["tasks"])]

    def worker(inp: WorkerInput):
        t = inp["task"]
        hits = doc_tools.search(t["query"], 4)
        body = llm.invoke([SystemMessage("너는 사내 기술 보고서의 한 섹션을 쓴다. 본문만 출력한다."), HumanMessage(
            f"보고서 '{inp['title']}'의 섹션 '{t['heading']}'. 목표: {t['goal']}\n"
            f"3~5문장으로 쓴다. 아래 근거만 쓰고, 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다).\n\n"
            f"[근거]\n{tech_report.format_chunks(hits)}")]).content
        return {"sections": [{"idx": inp["idx"], "heading": t["heading"], "body": body,
                              "allowed": [h["chunk_id"] for h in hits]}],
                "chunks": {h["chunk_id"]: h for h in hits}}

    def synthesize(state: State):
        report = sorted(state["sections"], key=lambda s: s["idx"])
        text = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in report)
        summary = llm.invoke([HumanMessage(
            f"아래 섹션들을 읽고 보고서 첫머리 요약을 3문장으로 쓴다. 본문에 없는 내용은 쓰지 않는다.\n\n{text}")]).content
        return {"report": report, "summary": summary}

    def save(state: State):
        path = tech_report.save_report(state["title"], [{"heading": s["heading"], "body": s["body"]} for s in state["report"]],
                                       summary=state["summary"], chunks=state["chunks"], stem="s2_04_orchestrator")
        return {"saved_path": str(path)}

    g = StateGraph(State)
    g.add_node("orchestrate", orchestrate)
    g.add_node("worker", worker)
    g.add_node("synthesize", synthesize)
    g.add_node("save", save)
    g.add_edge(START, "orchestrate")
    g.add_conditional_edges("orchestrate", assign, ["worker"])
    g.add_edge("worker", "synthesize")
    g.add_edge("synthesize", "save")
    g.add_edge("save", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    request = argv[0] if argv else REQUEST
    log = RunLog("s2_04_orchestrator_worker", meta={"request": request})
    state = log.stream(build_graph(get_llm()), {"request": request})
    log.write("end", {"title": state.get("title"), "tasks": state.get("tasks"),
                      "sections": [{"heading": s["heading"], "cited": tech_report.citations(s["body"]),
                                    "problems": tech_report.check_citations(s["body"], set(s["allowed"]))}
                                   for s in state.get("report", [])],
                      "saved_path": state.get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
