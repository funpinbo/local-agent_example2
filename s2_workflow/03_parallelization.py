"""2단계-03 Parallelization (Sectioning + Voting).

           ┌▶ write_0 ─┐                 ┌▶ vote_0 ─┐
    START ─┼▶ write_1 ─┼▶ join ─▶ fan ───┼▶ vote_1 ─┼▶ tally ─▶ save → END
           ├▶ write_2 ─┤                 └▶ vote_2 ─┘
           └▶ write_3 ─┘

- Sectioning: 섹션을 코드가 미리 정해 두고(4개), 섹션마다 검색·작성을 동시에 돌린다. 결과는 reducer(operator.add)로 모은다.
- Voting: 같은 검토를 3번 돌려 다수결로 통과 여부를 정한다.
- 속도: --mode sequential이면 같은 노드를 한 줄로 이어 돌린다. 두 모드의 시간을 비교한다.
  로컬 GPU 하나에서 Ollama가 요청을 동시에 처리하는지(OLLAMA_NUM_PARALLEL)에 따라 차이가 달라진다.

    python s2_workflow/03_parallelization.py [--mode parallel|sequential|both]
"""

from __future__ import annotations

import argparse
import operator
import sys
import time
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from common import doc_tools, tech_report
from common.runlog import RunLog

TITLE = "로컬 LLM 서버 도입 한 달 운영 보고"
# 코드가 미리 정한 섹션 (Orchestrator–Worker와 다른 점: 섹션 수와 내용을 실행 전에 안다)
SECTIONS = [
    ("구성과 담당", "로컬 LLM 서버 구성 GPU 서버 담당"),
    ("장애와 대응", "OOM 기동 실패 응답 지연 장애 대응"),
    ("운영 정책", "요청 로그 보관 기간 보안 검토"),
    ("향후 계획", "GPU 증설 코로케이션 대시보드 계획"),
]
VOTERS = 3


class Vote(BaseModel):
    ok: bool = Field(description="모든 문장이 인용한 근거로 뒷받침되면 true")
    reason: str = Field(description="판단 이유 한두 문장")


def merge(a, b):
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    sections: Annotated[list[dict], operator.add]
    chunks: Annotated[dict, merge]
    votes: Annotated[list[dict], operator.add]
    timings: Annotated[list[dict], operator.add]
    report: list[dict]
    passed: bool
    saved_path: str
    t0: float


def build_graph(llm, *, parallel: bool = True):
    def make_writer(i: int):
        heading, query = SECTIONS[i]

        def write(state: State):
            start = time.perf_counter()
            hits = doc_tools.search(query, 4)
            body = llm.invoke([SystemMessage("너는 사내 기술 보고서를 쓴다. 본문만 출력한다."), HumanMessage(
                f"보고서 '{TITLE}'의 섹션 '{heading}' 본문을 3~5문장으로 쓴다. "
                f"아래 근거만 쓰고, 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다).\n\n[근거]\n{tech_report.format_chunks(hits)}")]).content
            end = time.perf_counter()
            return {"sections": [{"idx": i, "heading": heading, "body": body}],
                    "chunks": {h["chunk_id"]: h for h in hits},
                    "timings": [{"node": f"write_{i}", "start": round(start - state["t0"], 2), "end": round(end - state["t0"], 2)}]}
        return write

    def make_voter(i: int):
        def vote(state: State):
            start = time.perf_counter()
            text = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in state["report"])
            used = {c for s in state["report"] for c in tech_report.citations(s["body"])}
            ev = tech_report.format_chunks([state["chunks"][c] for c in sorted(used) if c in state["chunks"]])
            v = Vote.model_validate(llm.with_structured_output(Vote).invoke([HumanMessage(
                f"보고서의 모든 문장이 인용한 근거 조각으로 뒷받침되는지 검토한다.\n\n[보고서]\n{text}\n\n[인용된 근거]\n{ev}")]))
            end = time.perf_counter()
            return {"votes": [{"voter": i, **v.model_dump()}],
                    "timings": [{"node": f"vote_{i}", "start": round(start - state["t0"], 2), "end": round(end - state["t0"], 2)}]}
        return vote

    def join(state: State):
        # reducer로 모인 섹션은 끝난 순서대로 쌓여 있다. 원래 순서로 정렬한다.
        return {"report": sorted(state["sections"], key=lambda s: s["idx"])}

    def tally(state: State):
        yes = sum(v["ok"] for v in state["votes"])
        return {"passed": yes * 2 > len(state["votes"])}

    def save(state: State):
        path = tech_report.save_report(TITLE, state["report"], chunks=state["chunks"],
                                       stem=f"s2_03_parallel_{'par' if parallel else 'seq'}")
        return {"saved_path": str(path)}

    g = StateGraph(State)
    writers = [f"write_{i}" for i in range(len(SECTIONS))]
    voters = [f"vote_{i}" for i in range(VOTERS)]
    for i, n in enumerate(writers):
        g.add_node(n, make_writer(i))
    for i, n in enumerate(voters):
        g.add_node(n, make_voter(i))
    g.add_node("join", join)
    g.add_node("tally", tally)
    g.add_node("save", save)
    if parallel:
        for n in writers:
            g.add_edge(START, n)
        g.add_edge(writers, "join")          # 모두 끝나야 join
        for n in voters:
            g.add_edge("join", n)
        g.add_edge(voters, "tally")
    else:
        chain = [START, *writers, "join", *voters, "tally"]
        for a, b in zip(chain, chain[1:]):
            g.add_edge(a, b)
    g.add_edge("tally", "save")
    g.add_edge("save", END)
    return g.compile()


def run(llm, mode: str) -> dict:
    graph = build_graph(llm, parallel=(mode == "parallel"))
    log = RunLog("s2_03_parallelization", meta={"mode": mode, "sections": len(SECTIONS), "voters": VOTERS})
    t0 = time.perf_counter()
    state = log.stream(graph, {"t0": t0})
    total = round(time.perf_counter() - t0, 2)
    result = {"mode": mode, "total_sec": total, "timings": state["timings"], "votes": state["votes"],
              "passed": state["passed"], "finish_order": [s["idx"] for s in state["sections"]],
              "cited": {s["heading"]: tech_report.citations(s["body"]) for s in state["report"]},
              "saved_path": state["saved_path"]}
    log.write("end", result)
    return result


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both", choices=["parallel", "sequential", "both"])
    args = ap.parse_args(argv)
    msdf.connect()
    llm = get_llm()
    doc_tools.search("워밍업", 1)          # 색인을 먼저 만들어 두어 두 모드 모두 검색은 캐시를 쓰게 한다
    modes = ["sequential", "parallel"] if args.mode == "both" else [args.mode]
    for m in modes:
        r = run(llm, m)
        print(f"{m}: {r['total_sec']}s, votes={[v['ok'] for v in r['votes']]}")


if __name__ == "__main__":
    main(sys.argv[1:])
