"""2단계-05 Evaluator–Optimizer.

    START → gather → generate → evaluate ─┬▶ save → END
                        ▲                 └▶ (고칠 점) generate      최대 3회차, 넘으면 남은 문제를 적고 저장

생성자와 평가자를 나눈다.
- 생성자: 근거 묶음으로 보고서 전체를 쓴다(구조화 출력). 2회차부터는 평가자의 지적을 받아 고친다.
- 평가자: 생성자의 대화를 보지 않는다. 문장 하나와 그 문장이 인용한 조각 원문만 받는다.
    1) 코드: 인용이 없는 문장, 근거 묶음에 없는 조각 id
    2) LLM: 인용한 조각이 그 문장을 실제로 뒷받침하는가(문장마다 따로)
1단계 05 Reflection은 같은 모델이 자기 초안을 같은 맥락에서 점검했다. 여기서는 평가를 따로 떼어 낸다.

    python s2_workflow/05_evaluator_optimizer.py
"""

from __future__ import annotations

import operator
import re
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

TITLE = "로컬 LLM 서버 도입 한 달 운영 보고"
QUERIES = ["로컬 LLM 서버 구성 담당", "OOM 기동 실패 응답 지연 장애 대응", "요청 로그 보관 기간", "GPU 증설 계획"]
MAX_ROUNDS = 3


class Report(BaseModel):
    sections: list[tech_report.Section] = Field(description="3~5개 섹션")


class Support(BaseModel):
    supported: bool = Field(description="근거 원문이 문장의 내용을 뒷받침하면 true")
    reason: str = Field(description="판단 이유 한 문장")


class State(TypedDict, total=False):
    chunks: dict
    sections: list[dict]
    round: int
    feedback: list[str]
    rounds: Annotated[list[dict], operator.add]
    outcome: str
    saved_path: str


def sentences(body: str) -> list[str]:
    """마침표·줄바꿈으로 나눈다. '…한다. [03_oom#1]'처럼 인용만 남은 조각은 앞 문장에 붙인다."""
    out: list[str] = []
    for p in re.split(r"(?<=[.!?])\s+|\n+", body.strip()):
        p = p.strip().lstrip("-• ").strip()
        if not p:
            continue
        if out and not tech_report.CITE.sub("", p).strip(" .,"):
            out[-1] = f"{out[-1]} {p}"
        elif len(p) > 3:
            out.append(p)
    return out


GEN_SYSTEM = ("너는 사내 기술 보고서를 쓴다. 근거 묶음에 있는 내용만 쓴다. "
              "문장마다 그 문장을 뒷받침하는 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다).")
EVAL_SYSTEM = "너는 사실 확인 담당이다. 문장과 근거 원문만 보고 판단한다. 근거에 없는 숫자·날짜·사실이 문장에 있으면 뒷받침되지 않는 것이다."


def build_graph(llm):
    def gather(state: State):
        chunks = {h["chunk_id"]: h for q in QUERIES for h in doc_tools.search(q, 3)}
        return {"chunks": chunks, "round": 0, "feedback": []}

    def generate(state: State):
        ask = f"보고서 '{TITLE}'를 섹션 3~5개로 쓴다.\n\n[근거 묶음]\n{tech_report.format_chunks(list(state['chunks'].values()))}"
        if state.get("feedback"):
            prev = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in state["sections"])
            ask += f"\n\n[이전 원고]\n{prev}\n\n[평가자 지적 — 이 문장들을 고치거나 지운다]\n" + "\n".join(f"- {f}" for f in state["feedback"])
        out = Report.model_validate(llm.with_structured_output(Report).invoke([SystemMessage(GEN_SYSTEM), HumanMessage(ask)]))
        return {"sections": [s.model_dump() for s in out.sections], "round": state["round"] + 1}

    def evaluate(state: State):
        chunks = state["chunks"]
        findings, eval_errors, checked, supported = [], [], 0, 0
        for sec in state["sections"]:
            for sent in sentences(sec["body"]):
                ids = tech_report.citations(sent)
                if not ids:
                    findings.append(f"[{sec['heading']}] 인용 없음: {sent}")
                    continue
                unknown = [i for i in ids if i not in chunks]
                if unknown:
                    findings.append(f"[{sec['heading']}] 없는 조각 {', '.join(unknown)}: {sent}")
                    continue
                checked += 1
                ev = tech_report.format_chunks([chunks[i] for i in ids])
                try:
                    s = Support.model_validate(llm.with_structured_output(Support).invoke([
                        SystemMessage(EVAL_SYSTEM), HumanMessage(f"[문장]\n{sent}\n\n[근거 원문]\n{ev}")]))
                except Exception as e:
                    # 평가자 호출이 실패해도 그래프 전체를 멈추지 않는다. 판정하지 못한 문장으로 따로 남긴다.
                    # (실제로 평가자의 구조화 출력이 같은 토큰을 반복하다 Ollama가 생성을 중단한 적이 있다)
                    eval_errors.append(f"[{sec['heading']}] {type(e).__name__}: {str(e)[:120]} — {sent}")
                    continue
                if s.supported:
                    supported += 1
                else:
                    findings.append(f"[{sec['heading']}] 근거와 맞지 않음({s.reason}): {sent}")
        rec = {"round": state["round"], "sentences_checked": checked, "supported": supported,
               "findings": findings, "eval_errors": eval_errors}
        return {"feedback": findings, "rounds": [rec]}

    def route(state: State) -> str:
        if not state["feedback"]:
            return "save"
        return "generate" if state["round"] < MAX_ROUNDS else "save"

    def save(state: State):
        path = tech_report.save_report(TITLE, state["sections"], chunks=state["chunks"], stem="s2_05_evaluator")
        return {"saved_path": str(path), "outcome": "clean" if not state["feedback"] else "max_rounds"}

    g = StateGraph(State)
    for name, fn in [("gather", gather), ("generate", generate), ("evaluate", evaluate), ("save", save)]:
        g.add_node(name, fn)
    g.add_edge(START, "gather")
    g.add_edge("gather", "generate")
    g.add_edge("generate", "evaluate")
    g.add_conditional_edges("evaluate", route, ["generate", "save"])
    g.add_edge("save", END)
    return g.compile()


def main(argv: list[str]) -> None:
    from common.llm import get_llm
    from infra.msdf.services import agent as msdf
    msdf.connect()
    log = RunLog("s2_05_evaluator_optimizer", meta={"queries": QUERIES, "max_rounds": MAX_ROUNDS})
    state = log.stream(build_graph(get_llm()), {})
    log.write("end", {"outcome": state.get("outcome"), "rounds": [
        {k: r[k] for k in ("round", "sentences_checked", "supported")} | {"findings": len(r["findings"]), "eval_errors": len(r.get("eval_errors", []))}
        for r in state.get("rounds", [])], "saved_path": state.get("saved_path")})


if __name__ == "__main__":
    main(sys.argv[1:])
