"""2단계 그래프의 연결과 흐름 검증 (가짜 LLM + MSDF local, GPU 없이)."""

import importlib.util
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from common import config, tech_report
from common.fake_llm import ScriptedChatModel, tool_call
from infra.msdf.services import agent

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    path = ROOT / "s2_workflow" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"s2_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    agent.close()
    agent.connect("local", db=tmp_path / "msdf.db")
    yield tmp_path
    agent.close()


def cite_first(text_if_none: str = "근거 없음"):
    """프롬프트에 보인 첫 조각 id를 인용한 문장을 돌려주는 가짜 응답 (병렬 노드에서도 순서와 무관)."""
    def respond(messages):
        text = messages[-1].content
        evidence = text.split("[근거]", 1)[1] if "[근거]" in text else ""   # 지시문의 예시 id는 건너뛴다
        ids = tech_report.citations(evidence)
        return f"내용입니다 [{ids[0]}]." if ids else text_if_none
    return respond


# ---------------- 00 ----------------
def test_00_rag_tool_records_queries_context_and_unretrieved_cites(env):
    m = load("00_rag_tool_limit")
    llm = ScriptedChatModel(responses=[
        tool_call("doc_search", {"query": "OOM 원인", "k": 3}),
        tool_call("doc_search", {"query": "로그 보관", "k": 3}),
        tool_call("save_tech_report", {"title": "운영 보고", "sections": [
            {"heading": "장애", "body": "메모리 부족 [03_oom#1]."},
            {"heading": "계획", "body": "증설 검토 [06_gpu_plan#1]."}]}),
        "저장했습니다.",
    ])
    state = m.build_graph(llm).invoke(m.inputs())
    r = m.analyze(state)
    assert r["queries"] == ["OOM 원인", "로그 보관"] and r["saved"]
    sizes = [c["chars"] for c in r["context"]]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0] * 2      # 검색 결과가 대화에 쌓인다
    plan = next(s for s in r["sections"] if s["heading"] == "계획")
    assert plan["not_retrieved"] == ["06_gpu_plan#1"]               # 찾지 않은 조각을 인용
    assert list(env.glob("s2_00_rag_tool_*.docx"))


# ---------------- 01 ----------------
def test_01_chain_gates_and_retry(env):
    m = load("01_prompt_chaining")
    outline = {"title": "운영 보고", "sections": [
        {"heading": "장애", "query": "OOM 원인"}, {"heading": "정책", "query": "로그 보관 기간"},
        {"heading": "계획", "query": "GPU 증설"}]}
    llm = ScriptedChatModel(responses=["인용 없는 본문입니다.", cite_first(), cite_first(), cite_first(), "요약입니다."],
                            structured=[outline])
    state = m.build_graph(llm).invoke(m.inputs(), {"recursion_limit": 100})
    assert state["outcome"] == "saved" and len(state["sections"]) == 3
    sec_gates = [g for g in state["gates"] if g["gate"] == "section"]
    assert sec_gates[0]["problems"] and not sec_gates[1]["problems"]     # 첫 섹션은 관문에서 한 번 되돌아감
    assert all(not s["unresolved"] for s in state["sections"])
    # 각 섹션 작성자는 자기 섹션의 근거만 봤다
    assert all(len(tech_report.citations(c[-1].content.split("[근거]", 1)[1])) <= 4
               for c in llm.calls if "[근거]" in c[-1].content)


def test_01_outline_gate_stops(env):
    m = load("01_prompt_chaining")
    bad = {"title": "t", "sections": [{"heading": "하나", "query": "OOM"}]}
    llm = ScriptedChatModel(responses=[], structured=[bad, bad])
    state = m.build_graph(llm).invoke(m.inputs())
    assert state["outcome"] == "outline_rejected" and len(state["gates"]) == 2


# ---------------- 02 ----------------
def test_02_routes_to_analysis(env):
    m = load("02_routing")
    llm = ScriptedChatModel(responses=[], structured=[
        {"kind": "analysis", "reason": "한 장애의 원인 분석"},
        {"sections": [{"heading": "현상", "body": "서버가 내려갔다 [03_oom#0]."},
                      {"heading": "원인", "body": "메모리 설정 [03_oom#1]."},
                      {"heading": "대응", "body": "0.85부터 [03_oom#2]."}]}])
    state = m.build_graph(llm).invoke({"request": "OOM 장애 원인 분석"})
    assert state["kind"] == "analysis" and [s["heading"] for s in state["sections"]] == ["현상", "원인", "대응"]
    assert state["problems"] == [] and Path(state["saved_path"]).name.startswith("s2_02_routing_analysis")


def test_02_routes_to_comparison(env):
    m = load("02_routing")
    llm = ScriptedChatModel(responses=[], structured=[
        {"kind": "comparison", "reason": "두 안 비교"},
        {"a": "90일 보관", "b": "60일 보관", "criteria": ["보안", "장애 분석"]},
        {"sections": [{"heading": "90일", "body": "반려됨 [04_logging#1]."},
                      {"heading": "60일", "body": "승인됨 [04_logging#1]."},
                      {"heading": "비교", "body": "60일로 정리 [99_none#9]."}]}])
    state = m.build_graph(llm).invoke({"request": "90일과 60일 비교"})
    assert state["kind"] == "comparison" and state["title"].startswith("90일 보관")
    assert any("99_none#9" in p for p in state["problems"])


# ---------------- 03 ----------------
@pytest.mark.parametrize("parallel", [True, False])
def test_03_sections_reducer_and_vote(env, parallel):
    m = load("03_parallelization")
    llm = ScriptedChatModel(responses=[cite_first()] * len(m.SECTIONS),
                            structured=[{"ok": True, "reason": "a"}, {"ok": False, "reason": "b"}, {"ok": True, "reason": "c"}])
    import time
    state = m.build_graph(llm, parallel=parallel).invoke({"t0": time.perf_counter()})
    assert [s["idx"] for s in state["report"]] == [0, 1, 2, 3]
    assert len(state["votes"]) == 3 and state["passed"] is True        # 2:1 다수결
    assert {t["node"] for t in state["timings"]} >= {"write_0", "write_3", "vote_2"}


# ---------------- 04 ----------------
def test_04_send_makes_workers_at_runtime(env):
    m = load("04_orchestrator_worker")

    def respond(messages):
        if "첫머리 요약" in messages[-1].content:
            return "요약입니다."
        return cite_first()(messages)

    plan = {"title": "운영 보고", "tasks": [
        {"heading": h, "query": q, "goal": "정리"} for h, q in
        [("구성", "서버 구성"), ("장애", "OOM"), ("정책", "로그 보관"), ("검색", "검색 API 지연"), ("계획", "GPU 증설")]]}
    llm = ScriptedChatModel(responses=[respond] * 6, structured=[plan])
    state = m.build_graph(llm).invoke({"request": "운영 보고"})
    assert len(state["report"]) == 5 and [s["idx"] for s in state["report"]] == list(range(5))
    assert all(not tech_report.check_citations(s["body"], set(s["allowed"])) for s in state["report"])
    assert state["summary"] == "요약입니다." and Path(state["saved_path"]).exists()


# ---------------- 05 ----------------
def test_05_evaluator_feedback_loop(env, monkeypatch):
    m = load("05_evaluator_optimizer")
    fixed = [{"chunk_id": f"03_oom#{i}", "source": "03_oom.md", "heading": h, "text": t}
             for i, (h, t) in enumerate([("증상", "OOM"), ("주요 원인", "설정"), ("대응", "0.85부터")])]
    monkeypatch.setattr(m.doc_tools, "search", lambda q, k=4: fixed)
    r1 = {"sections": [{"heading": "장애", "body": "메모리 설정이 원인이다 [03_oom#1]. 인용 없는 문장이다."}]}
    r2 = {"sections": [{"heading": "장애", "body": "메모리 설정이 원인이다 [03_oom#1]. 대응은 0.85부터다 [03_oom#2]."}]}
    llm = ScriptedChatModel(responses=[], structured=[
        r1, {"supported": True, "reason": "맞음"},
        r2, {"supported": True, "reason": "맞음"}, {"supported": True, "reason": "맞음"}])
    state = m.build_graph(llm).invoke({})
    first, second = state["rounds"]
    assert any("인용 없음" in f for f in first["findings"])
    assert second["findings"] == [] and second["supported"] == 2
    assert state["outcome"] == "clean"
    # 평가자는 문장 하나와 그 근거만 받는다 (생성자 원고 전체를 보지 않는다)
    eval_inputs = [c for c in llm.calls] if llm.calls else []
    assert not eval_inputs   # 구조화 출력만 썼으므로 일반 호출 없음


def test_05_sentence_split_keeps_trailing_citation():
    m = load("05_evaluator_optimizer")
    assert m.sentences("원인은 설정이다. [03_oom#1]\n대응은 0.85다 [03_oom#2].") == [
        "원인은 설정이다. [03_oom#1]", "대응은 0.85다 [03_oom#2]."]
