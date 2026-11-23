"""3단계-04 Sequential · Parallel (수집은 병렬, 작성은 순차).

           ┌▶ mail_agent ─────┐
    START ─┼▶ schedule_agent ─┼▶ join → write_0 → write_1 → write_2 → write_3 → save → END
           └▶ approval_agent ─┘

- 수집 Agent 셋은 서로의 결과가 필요 없으므로 동시에 돌린다(--collect sequential이면 한 줄로).
- 작성은 섹션을 차례로 쓴다. 뒤 섹션이 앞 섹션을 보고 겹치지 않게 써야 하므로 순서가 있다.
- 실행하는 동안 GPU 메모리를 0.5초마다 재고, 끝나면 `ollama ps`를 남긴다.
  1편: 로컬 GPU 하나에서는 동시 요청이 늘면 KV cache 자리가 같이 늘고, Ollama가 요청을 줄 세우기도 한다.

    python s3_multi/04_sequential_parallel.py [--collect parallel|sequential|both]
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
from typing_extensions import TypedDict

from common import gpu, gw_tools, monthly_report
from common.agents import react_agent, run_agent
from common.runlog import RunLog

MONTH = "2026-10"
TITLE = "2026년 10월 월간 업무 보고 (이재휘)"
WORKERS = {"mail_agent": (gw_tools.MAIL_TOOLS, "메일 담당", "10월 메일에서 주요 업무, 장애, 요청 사항을 정리한다."),
           "schedule_agent": (gw_tools.EVENT_TOOLS, "일정 담당", "10월 일정에서 회의·출장·교육을 정리한다."),
           "approval_agent": (gw_tools.APPROVAL_TOOLS, "결재 담당", "10월 결재 문서와 결과(승인·반려 사유)를 정리한다.")}
WORKER_SYSTEM = ("너는 {role}이다. 도구를 한 번에 하나씩 써서 그룹웨어 기록을 확인한다. 받은 일에 맞게 확인한 내용을 항목별로 정리해 답한다. "
                 "항목마다 [mail:12], [event:3], [approval:5]처럼 근거 id를 단다. 기록에 없는 내용은 쓰지 않는다.")
SECTIONS = ["주요 업무", "장애와 대응", "결재 진행", "다음 달 계획"]


def merge(a, b):
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    t0: float
    notes: Annotated[dict, merge]
    seen: Annotated[dict, merge]
    sections: Annotated[list[dict], operator.add]
    timings: Annotated[list[dict], operator.add]
    problems: list[str]
    saved_path: str


def build_graph(llm, *, parallel: bool = True):
    agents = {n: react_agent(llm, t, WORKER_SYSTEM.format(role=r)) for n, (t, r, _) in WORKERS.items()}

    def timed(name, fn):
        def node(state: State):
            s = time.perf_counter()
            out = fn(state)
            out["timings"] = [{"node": name, "start": round(s - state["t0"], 2), "end": round(time.perf_counter() - state["t0"], 2)}]
            return out
        return node

    def make_worker(name):
        def work(state: State):
            r = run_agent(agents[name], f"{WORKERS[name][2]}\n(대상 기간: {MONTH})")
            return {"notes": {name: r["note"]}, "seen": {name: r["seen"]}}
        return work

    def join(state: State):
        return {}

    def make_writer(i: int):
        heading = SECTIONS[i]

        def write(state: State):
            notes = "\n\n".join(f"[{n}]\n{state['notes'].get(n, '')}" for n in WORKERS)
            before = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in state.get("sections", [])) or "(아직 없음)"
            body = llm.invoke([SystemMessage("너는 월간 보고의 한 섹션을 쓴다. 본문만 출력한다."), HumanMessage(
                f"보고서 '{TITLE}'의 섹션 '{heading}'을 3~5문장으로 쓴다. 앞 섹션과 겹치는 내용은 쓰지 않는다. "
                f"메모의 근거 id를 문장마다 [mail:12]처럼 그대로 단다.\n\n[앞 섹션]\n{before}\n\n[담당별 메모]\n{notes}")]).content
            return {"sections": [{"heading": heading, "body": body}]}
        return write

    def save(state: State):
        known = {r for ids in state["seen"].values() for r in ids}
        problems = [f"{s['heading']}: {p}" for s in state["sections"] for p in monthly_report.check_refs(s["body"], known)]
        path = monthly_report.save(TITLE, state["sections"], stem=f"s3_04_{'par' if parallel else 'seq'}")
        return {"problems": problems, "saved_path": str(path)}

    g = StateGraph(State)
    for n in WORKERS:
        g.add_node(n, timed(n, make_worker(n)))
    g.add_node("join", join)
    writers = [f"write_{i}" for i in range(len(SECTIONS))]
    for i, n in enumerate(writers):
        g.add_node(n, timed(n, make_writer(i)))
    g.add_node("save", save)
    if parallel:
        for n in WORKERS:
            g.add_edge(START, n)
        g.add_edge(list(WORKERS), "join")
    else:
        chain = [START, *WORKERS, "join"]
        for a, b in zip(chain, chain[1:]):
            g.add_edge(a, b)
    chain = ["join", *writers, "save"]
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    g.add_edge("save", END)
    return g.compile()


def run(llm, mode: str) -> dict:
    graph = build_graph(llm, parallel=(mode == "parallel"))
    log = RunLog("s3_04_sequential_parallel", meta={"collect": mode})
    with gpu.Sampler() as smp:
        t0 = time.perf_counter()
        state = log.stream(graph, {"t0": t0}, {"recursion_limit": 40})
        total = round(time.perf_counter() - t0, 2)
    result = {"collect": mode, "total_sec": total, "timings": state["timings"], "gpu": smp.summary(),
              "gpu_series": smp.samples, "ollama_ps": gpu.ollama_ps(), "problems": state["problems"],
              "notes_chars": {n: len(t) for n, t in state["notes"].items()}, "saved_path": state["saved_path"]}
    log.write("end", result)
    return result


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", default="both", choices=["parallel", "sequential", "both"])
    args = ap.parse_args(argv)
    msdf.connect()
    llm = get_llm()
    for kind in ("mails", "events", "approvals"):       # 워밍업: 수집 데이터는 MSDF 캐시를 쓰게 한다
        getattr(gw_tools, kind)(MONTH)
    for m in (["sequential", "parallel"] if args.collect == "both" else [args.collect]):
        r = run(llm, m)
        print(f"{m}: {r['total_sec']}s gpu={r['gpu']}")


if __name__ == "__main__":
    main(sys.argv[1:])
