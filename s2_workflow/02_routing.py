"""2단계-02 Routing.

    START → classify ─┬▶ summary_chain    ─┐
                      ├▶ analysis_chain   ─┼▶ save → END
                      └▶ comparison_chain ─┘

시작할 때 요청을 한 번 분류하고, 종류마다 따로 만든 체인(프롬프트·검색 방식)으로 보낸다.
1단계 Conditional Branch는 루프 안에서 LLM 출력으로 갈라졌다. Routing은 일을 시작하기 전에 입력을 분류한다.

    python s2_workflow/02_routing.py                 # 요청 3개를 차례로
    python s2_workflow/02_routing.py "요청 문장"
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import doc_tools, tech_report
from common.runlog import RunLog

SAMPLES = [
    ("사내 로컬 LLM 서버 문서 전체를 한 문단으로 요약해 줘.", "summary"),
    ("2호기에서 난 메모리 부족(OOM) 장애의 원인과 대응을 분석해 줘.", "analysis"),
    ("요청 로그 보관 기간 90일안과 60일안을 비교해 줘.", "comparison"),
]


class Route(BaseModel):
    kind: Literal["summary", "analysis", "comparison"] = Field(
        description="summary: 전체·주제 요약 / analysis: 한 사건·문제의 원인과 대응 분석 / comparison: 두 안·대상 비교")
    reason: str = Field(description="그렇게 고른 이유 한 문장")


class Targets(BaseModel):
    a: str = Field(description="비교 대상 1")
    b: str = Field(description="비교 대상 2")
    criteria: list[str] = Field(description="비교 기준 2~4개")


class Sections(BaseModel):
    sections: list[tech_report.Section]


class State(TypedDict, total=False):
    request: str
    kind: str
    reason: str
    title: str
    sections: list[dict]
    chunks: dict
    problems: list[str]
    saved_path: str


CITE_RULE = "아래 근거만 쓰고, 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다). 근거에 없는 내용은 쓰지 않는다."


def build_graph(llm):
    def classify(state: State):
        r = Route.model_validate(llm.with_structured_output(Route).invoke([
            SystemMessage("너는 요청을 분류한다."), HumanMessage(state["request"])]))
        return {"kind": r.kind, "reason": r.reason}

    def route(state: State) -> str:
        return f"{state['kind']}_chain"

    def summary_chain(state: State):
        # 요약: 문서 목록을 보고 넓게 찾는다
        hits = doc_tools.search(state["request"], 8)
        body = llm.invoke([HumanMessage(
            f"[요청]\n{state['request']}\n\n[문서 목록]\n{doc_tools.catalog_text()}\n\n"
            f"한 문단(4~6문장)으로 요약한다. {CITE_RULE}\n\n[근거]\n{tech_report.format_chunks(hits)}")]).content
        return {"title": "사내 문서 요약", "sections": [{"heading": "요약", "body": body}],
                "chunks": {h["chunk_id"]: h for h in hits}}

    def analysis_chain(state: State):
        # 분석: 한 사건을 현상 → 원인 → 대응 순서로
        hits = doc_tools.search(state["request"], 5)
        out = Sections.model_validate(llm.with_structured_output(Sections).invoke([HumanMessage(
            f"[요청]\n{state['request']}\n\n'현상', '원인', '대응' 세 섹션으로 분석한다. {CITE_RULE}\n\n"
            f"[근거]\n{tech_report.format_chunks(hits)}")]))
        return {"title": "장애 분석", "sections": [s.model_dump() for s in out.sections],
                "chunks": {h["chunk_id"]: h for h in hits}}

    def comparison_chain(state: State):
        # 비교: 대상 둘을 뽑아 각각 찾은 뒤 기준별로 비교
        t = Targets.model_validate(llm.with_structured_output(Targets).invoke([HumanMessage(
            f"다음 요청에서 비교할 두 대상과 비교 기준을 뽑는다.\n{state['request']}")]))
        hits = {h["chunk_id"]: h for q in (t.a, t.b) for h in doc_tools.search(q, 4)}
        out = Sections.model_validate(llm.with_structured_output(Sections).invoke([HumanMessage(
            f"[요청]\n{state['request']}\n\n'{t.a}', '{t.b}', '비교({', '.join(t.criteria)})' 세 섹션으로 쓴다. {CITE_RULE}\n\n"
            f"[근거]\n{tech_report.format_chunks(list(hits.values()))}")]))
        return {"title": f"{t.a}와 {t.b} 비교", "sections": [s.model_dump() for s in out.sections], "chunks": hits}

    def save(state: State):
        allowed = set(state["chunks"])
        problems = [f"{s['heading']}: {p}" for s in state["sections"]
                    for p in tech_report.check_citations(s["body"], allowed)]
        path = tech_report.save_report(state["title"], state["sections"], chunks=state["chunks"],
                                       stem=f"s2_02_routing_{state['kind']}")
        return {"problems": problems, "saved_path": str(path)}

    g = StateGraph(State)
    for name, fn in [("classify", classify), ("summary_chain", summary_chain), ("analysis_chain", analysis_chain),
                     ("comparison_chain", comparison_chain), ("save", save)]:
        g.add_node(name, fn)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route, ["summary_chain", "analysis_chain", "comparison_chain"])
    for n in ("summary_chain", "analysis_chain", "comparison_chain"):
        g.add_edge(n, "save")
    g.add_edge("save", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    graph = build_graph(get_llm())
    cases = [(argv[0], None)] if argv else SAMPLES
    for request, expect in cases:
        log = RunLog("s2_02_routing", meta={"request": request, "expect": expect})
        state = log.stream(graph, {"request": request})
        log.write("end", {"kind": state.get("kind"), "expect": expect, "reason": state.get("reason"),
                          "sections": [s["heading"] for s in state.get("sections", [])],
                          "problems": state.get("problems"), "saved_path": state.get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
