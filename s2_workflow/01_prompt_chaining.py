"""2단계-01 Prompt Chaining.

    START → outline → gate_outline ─(통과)─▶ search → write → gate_section ─┬▶ (다음 섹션) search
                         └─(1번 다시)▶ outline        ▲            └─(1번 다시)▶ write        └▶ summarize → save → END
                         └─(또 실패)▶ END

일을 코드가 정한 단계로 나누고, 각 LLM 호출은 앞 단계의 결과만 받는다.
단계 사이에 코드 관문(gate)을 둔다: 목차 모양 검사, 섹션 인용 검사.
섹션마다 그 섹션의 검색 결과만 보여 주므로 00처럼 검색 결과가 한 대화에 쌓이지 않는다.

    python s2_workflow/01_prompt_chaining.py
"""

from __future__ import annotations

import operator
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import doc_tools, tech_report
from common.runlog import RunLog

REQUEST = "로컬 LLM 서버 도입 한 달의 운영 현황을 사내 문서를 근거로 기술 보고서로 정리해 줘. 장애와 대응, 운영 정책, 앞으로의 계획을 포함해 줘."
MIN_SECTIONS, MAX_SECTIONS = 3, 6


class OutlineItem(BaseModel):
    heading: str = Field(description="섹션 제목")
    query: str = Field(description="이 섹션의 근거를 찾을 사내 문서 검색어")


class Outline(BaseModel):
    title: str = Field(description="보고서 제목")
    sections: list[OutlineItem] = Field(description=f"섹션 {MIN_SECTIONS}~{MAX_SECTIONS}개")


def merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    request: str
    title: str
    outline: list[dict]
    outline_tries: int
    index: int
    hits: list[dict]
    draft: str
    write_tries: int
    feedback: str
    sections: Annotated[list[dict], operator.add]
    chunks: Annotated[dict, merge]
    gates: Annotated[list[dict], operator.add]     # 관문 기록
    summary: str
    saved_path: str
    outcome: str


def outline_problems(outline: dict) -> list[str]:
    secs = outline.get("sections", [])
    p = []
    if not MIN_SECTIONS <= len(secs) <= MAX_SECTIONS:
        p.append(f"섹션은 {MIN_SECTIONS}~{MAX_SECTIONS}개여야 합니다(지금 {len(secs)}개).")
    heads = [s["heading"].strip() for s in secs]
    if len(set(heads)) != len(heads):
        p.append("섹션 제목이 겹칩니다.")
    if any(not s["query"].strip() for s in secs):
        p.append("검색어가 빈 섹션이 있습니다.")
    return p


def build_graph(llm):
    def outline(state: State):
        ask = (f"[요청]\n{state['request']}\n\n[사내 문서 목록]\n{doc_tools.catalog_text()}\n\n"
               f"보고서 제목과 섹션 {MIN_SECTIONS}~{MAX_SECTIONS}개를 정한다. 섹션마다 근거를 찾을 검색어를 붙인다.")
        if state.get("feedback"):
            ask += f"\n\n[고칠 점]\n{state['feedback']}"
        out = Outline.model_validate(llm.with_structured_output(Outline).invoke([HumanMessage(ask)]))
        return {"title": out.title, "outline": [s.model_dump() for s in out.sections],
                "outline_tries": state.get("outline_tries", 0) + 1, "index": 0, "feedback": ""}

    def gate_outline(state: State):
        p = outline_problems({"sections": state["outline"]})
        return {"gates": [{"gate": "outline", "try": state["outline_tries"], "problems": p}],
                "feedback": "\n".join(p)}

    def route_outline(state: State) -> str:
        if not state["feedback"]:
            return "search"
        return "outline" if state["outline_tries"] < 2 else "stop"

    def search(state: State):
        # 관문: 검색 결과가 비면 섹션 제목으로 한 번 더 찾는다.
        # (처음엔 이 관문이 없어서, 목차 LLM이 검색어로 파일 이름을 적자 근거 0개로 섹션을 쓰고 id를 지어냈다)
        item = state["outline"][state["index"]]
        query, hits = item["query"], doc_tools.search(item["query"], 4)
        if not hits:
            query, hits = item["heading"], doc_tools.search(item["heading"], 4)
        gate = {"gate": "search", "index": state["index"], "query": query, "hits": len(hits),
                "problems": [] if hits else ["검색 결과가 없습니다."]}
        return {"hits": hits, "chunks": {h["chunk_id"]: h for h in hits}, "write_tries": 0, "feedback": "",
                "gates": [gate]}

    def write(state: State):
        item = state["outline"][state["index"]]
        ask = (f"보고서 '{state['title']}'의 섹션 '{item['heading']}' 본문을 3~6문장으로 쓴다.\n"
               f"아래 근거만 쓰고, 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다). 근거에 없는 내용은 쓰지 않는다.\n\n"
               f"[근거]\n{tech_report.format_chunks(state['hits'])}")
        if state.get("feedback"):
            ask += f"\n\n[고칠 점]\n{state['feedback']}"
        body = llm.invoke([SystemMessage("너는 사내 기술 보고서를 쓴다. 본문만 출력한다."), HumanMessage(ask)]).content
        return {"draft": body, "write_tries": state.get("write_tries", 0) + 1}

    def gate_section(state: State):
        allowed = {h["chunk_id"] for h in state["hits"]}
        p = tech_report.check_citations(state["draft"], allowed)
        return {"gates": [{"gate": "section", "index": state["index"], "try": state["write_tries"], "problems": p}],
                "feedback": "\n".join(p)}

    def route_section(state: State) -> str:
        if state["feedback"] and state["write_tries"] < 2:
            return "write"
        return "accept"

    def accept(state: State):
        item = state["outline"][state["index"]]
        sec = {"heading": item["heading"], "body": state["draft"],
               "unresolved": state["feedback"].split("\n") if state["feedback"] else []}
        return {"sections": [sec], "index": state["index"] + 1, "feedback": ""}

    def route_next(state: State) -> str:
        return "search" if state["index"] < len(state["outline"]) else "summarize"

    def summarize(state: State):
        bodies = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in state["sections"])
        ask = f"아래 보고서 본문을 3문장으로 요약한다. 본문에 없는 내용은 쓰지 않는다. 인용 [id]는 그대로 둬도 된다.\n\n{bodies}"
        return {"summary": llm.invoke([HumanMessage(ask)]).content}

    def save(state: State):
        path = tech_report.save_report(state["title"], state["sections"], summary=state["summary"],
                                       chunks=state["chunks"], stem="s2_01_prompt_chaining")
        return {"saved_path": str(path), "outcome": "saved"}

    def stop(state: State):
        return {"outcome": "outline_rejected"}

    g = StateGraph(State)
    for name, fn in [("outline", outline), ("gate_outline", gate_outline), ("search", search), ("write", write),
                     ("gate_section", gate_section), ("accept", accept), ("summarize", summarize),
                     ("save", save), ("stop", stop)]:
        g.add_node(name, fn)
    g.add_edge(START, "outline")
    g.add_edge("outline", "gate_outline")
    g.add_conditional_edges("gate_outline", route_outline, ["search", "outline", "stop"])
    g.add_edge("search", "write")
    g.add_edge("write", "gate_section")
    g.add_conditional_edges("gate_section", route_section, ["write", "accept"])
    g.add_conditional_edges("accept", route_next, ["search", "summarize"])
    g.add_edge("summarize", "save")
    g.add_edge("save", END)
    g.add_edge("stop", END)
    return g.compile()


def inputs(request: str = REQUEST) -> dict:
    return {"request": request}


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    log = RunLog("s2_01_prompt_chaining", meta={"request": REQUEST})
    state = log.stream(build_graph(get_llm()), inputs(), {"recursion_limit": 100})
    log.write("end", {"outcome": state.get("outcome"), "title": state.get("title"),
                      "sections": [{"heading": s["heading"], "cited": tech_report.citations(s["body"]),
                                    "unresolved": s["unresolved"]} for s in state.get("sections", [])],
                      "gates": state.get("gates"), "saved_path": state.get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
