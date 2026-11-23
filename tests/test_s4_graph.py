"""4단계 그래프의 연결과 흐름 검증 (가짜 LLM + 그룹웨어 서버 + MSDF local, GPU 없이)."""

import importlib.util
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from langgraph.types import Command

from common import graph_shape, monthly_report
from common.fake_llm import ScriptedChatModel

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    path = ROOT / "s4_graph" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"s4_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def section_brain(no_ref_heading: str | None = None):
    """[기록]에 보인 첫 id를 인용한다. no_ref_heading 섹션은 처음 한 번 인용 없이 쓴다."""
    state = {"skipped": False}

    def respond(messages):
        text = messages[-1].content
        ev = text.split("[기록]", 1)[1] if "[기록]" in text else ""
        if no_ref_heading and f"'{no_ref_heading}'" in text and "[고칠 점]" not in text and not state["skipped"]:
            state["skipped"] = True
            return "근거 없이 쓴 문장이다."
        refs = monthly_report.refs(ev)
        return f"내용이다 [{refs[0]}]." if refs else "기록이 없다."
    return respond


def plan(*steps):
    return {"steps": [dict(zip(("action", "heading", "sources"), s)) for s in steps]}


NOV_PLAN = plan(("collect_mails", "", []), ("collect_approvals", "", []),
                ("write_section", "주요 업무", ["mails"]), ("write_section", "결재 진행", ["approvals"]), ("finish", "", []))
NOV_REPLAN = plan(("write_section", "주요 업무", ["mails"]), ("write_section", "결재 준비 상황", ["mails"]), ("finish", "", []))


# ---------------- 01 ----------------
def test_01_shapes_and_cyclic_termination(gw_env):
    m = load("01_dag_cyclic")
    dag = m.build_graph(ScriptedChatModel(responses=[section_brain("주요 업무")] * 4), cyclic=False)
    assert graph_shape.kind(dag) == "DAG"
    s = dag.invoke({"month": "2026-10"})
    assert s["stop_reason"] == "DAG: 한 번 지나감" and "주요 업무" in s["problems"]

    cyc = m.build_graph(ScriptedChatModel(responses=[section_brain("주요 업무")] * 5), cyclic=True)
    assert graph_shape.kind(cyc) == "Cyclic" and any("revise" in c for c in graph_shape.cycles(cyc))
    s = cyc.invoke({"month": "2026-10"})
    assert [r["problems"] for r in s["rounds"]] == [1, 0] and s["stop_reason"] == "문제 없음"


def test_01_stops_when_no_improvement(gw_env):
    m = load("01_dag_cyclic")
    always_bad = lambda msgs: "근거 없음." if "'결재 진행'" in msgs[-1].content else section_brain()(msgs)
    s = m.build_graph(ScriptedChatModel(responses=[always_bad] * 6), cyclic=True).invoke({"month": "2026-10"})
    assert len(s["rounds"]) == 2 and s["stop_reason"] == "고쳐도 문제 수가 줄지 않음"


# ---------------- 02 ----------------
def test_02_routes_depend_on_collected_data(gw_env):
    m = load("02_dynamic_routing")
    g = m.build_graph(ScriptedChatModel(responses=[section_brain()] * 8))
    octo = g.invoke({"month": "2026-10"})
    nov = g.invoke({"month": "2026-11"})
    assert "결재 진행" in octo["route"] and "장애와 대응" in octo["route"]
    assert "결재 진행(기록 없음 안내)" in nov["route"] and "결재 진행(기록 없음 안내)" not in octo["route"]
    note = next(s for s in nov["report"] if s["heading"] == "결재 진행")
    assert "결재 문서가 없다" in note["body"]
    assert [s["idx"] for s in nov["report"]] == sorted(s["idx"] for s in nov["report"])


# ---------------- 03 ----------------
def test_03_replans_when_approvals_empty(gw_env):
    m = load("03_planner_executor")
    llm = ScriptedChatModel(responses=[section_brain()] * 4, structured=[NOV_PLAN, NOV_REPLAN])
    s = m.build_graph(llm).invoke(m.inputs("2026-11"), {"recursion_limit": 80})
    assert [p["kind"] for p in s["plans"]] == ["plan", "replan"] and s["replans"] == 1
    assert any("결재 기록이 0건" in o for o in s["observations"])
    assert [x["heading"] for x in s["sections"]] == ["주요 업무", "결재 준비 상황"]
    assert s["outcome"] == "saved"


def test_03_bad_replan_twice_keeps_original_plan(gw_env):
    m = load("03_planner_executor")
    no_finish = plan(("write_section", "주요 업무", ["mails"]))          # finish가 빠진 새 계획
    llm = ScriptedChatModel(responses=[section_brain()] * 3, structured=[NOV_PLAN, no_finish, no_finish])
    s = m.build_graph(llm).invoke(m.inputs("2026-11"), {"recursion_limit": 80})
    assert [p["kind"] for p in s["plans"]] == ["plan", "replan", "replan"] and s["replans"] == 1
    assert any("원래 계획대로" in o for o in s["observations"])
    assert [x["heading"] for x in s["sections"]] == ["주요 업무", "결재 진행"]     # 빈 보고서가 아니다


def test_03_plan_gate_rejects_uncollected_sources(gw_env):
    m = load("03_planner_executor")
    bad = plan(("write_section", "주요 업무", ["mails"]), ("finish", "", []))
    good = plan(("collect_mails", "", []), ("write_section", "주요 업무", ["mails"]), ("finish", "", []))
    llm = ScriptedChatModel(responses=[section_brain()], structured=[bad, good])
    s = m.build_graph(llm).invoke(m.inputs("2026-10"))
    assert s["plan_tries"] == 2 and s["replans"] == 0 and s["outcome"] == "saved"
    assert s["plans"][0]["steps"][0]["action"] == "write_section" and s["plan_problems"] == []
    assert m.plan_problems(bad["steps"])[0].endswith("아직 모으지 않은 자료(mails)를 씁니다.")


# ---------------- 04 ----------------
@pytest.mark.parametrize("answers,outcome", [
    ([{"action": "approve"}], "submitted"),
    ([{"action": "reject"}], "not_submitted"),
    ([{"action": "edit", "changes": {"title": "10월 보고(수정)"}}, {"action": "approve"}], "submitted"),
])
def test_04_interrupt_then_resume(gw_env, answers, outcome):
    m = load("04_human_in_the_loop")
    g = m.build_graph(ScriptedChatModel(responses=[section_brain()] * 4))
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    g.invoke({"month": "2026-10"}, cfg)
    assert g.get_state(cfg).next == ("human",)                 # 상신 전에 멈춰 있다
    before = httpx.get(f"{gw_env}/api/approvals", headers={"X-User": "jiyoung"}, params={"role": "approver"}).json()
    for a in answers:
        g.invoke(Command(resume=a), cfg)
    s = g.get_state(cfg).values
    after = httpx.get(f"{gw_env}/api/approvals", headers={"X-User": "jiyoung"}, params={"role": "approver"}).json()
    assert s["outcome"] == outcome
    if outcome == "submitted":
        new = after[-1]
        assert len(after) == len(before) + 1 and new["status"] == "pending" and new["line"] == ["jiyoung", "dongho"]
        assert new["title"] == s["draft"]["title"]
        att = httpx.get(f"{gw_env}/api/approvals/{new['id']}/attachment", headers={"X-User": "jiyoung"})
        assert att.status_code == 200 and att.content[:2] == b"PK"            # docx(zip)
    else:
        assert len(after) == len(before)


# ---------------- 05 ----------------
def test_05_full_graph_end_to_end(gw_env):
    m = load("05_full_graph")
    llm = ScriptedChatModel(responses=[section_brain()] * 6, structured=[NOV_PLAN, NOV_REPLAN])
    g = m.build_graph(llm)
    assert graph_shape.kind(g) == "Cyclic" and "planner_executor" in graph_shape.mermaid(g)
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": 60}
    g.invoke({"month": "2026-11"}, cfg)
    assert g.get_state(cfg).next == ("human",)
    g.invoke(Command(resume={"action": "approve"}), cfg)
    s = g.get_state(cfg).values
    assert s["outcome"] == "submitted" and s["pe"]["replans"] == 1 and s["submitted"]["next"] == "jiyoung"
    assert "plans" not in s                                     # Subgraph 안의 State는 올라오지 않는다
