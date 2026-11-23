"""3단계 그래프의 연결과 흐름 검증 (가짜 LLM + 그룹웨어 서버 + MSDF local, GPU 없이)."""

import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from langchain_core.messages import SystemMessage, ToolMessage

from common import config, monthly_report
from common.fake_llm import ScriptedChatModel, tool_call
from infra.groupware.app import create_app
from infra.groupware.seed import seed
from infra.msdf.services import agent, groupware_sync

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    path = ROOT / "s3_multi" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"s3_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, pg_schema):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(schema=pg_schema), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(groupware_sync, "GROUPWARE_URL", url)
    monkeypatch.setattr(config, "GROUPWARE_URL", url)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    agent.close()
    agent.connect("local", db=tmp_path / "msdf.db")
    yield tmp_path
    agent.close()
    server.should_exit = True
    th.join(5)


def brain(messages):
    """가짜 담당 Agent: 처음엔 자기 목록 도구를 부르고, 결과를 받으면 첫 근거 id로 메모를 쓴다."""
    system = messages[0].content if isinstance(messages[0], SystemMessage) else ""
    last = messages[-1]
    if "한 섹션" in system:
        return "내용입니다 [mail:1]."
    if isinstance(last, ToolMessage):
        ref = (monthly_report.refs_loose(str(last.content)) or ["mail:0"])[0]
        return f"- 정리한 항목 [{ref}]"
    tool = "gw_list_mails" if "메일" in system else "gw_list_events" if "일정" in system else "gw_list_approvals"
    return tool_call(tool, {"month": "2026-10"})


REPORT = {"title": "10월 보고", "sections": [{"heading": "주요 업무", "body": "킥오프 [mail:1]."},
                                           {"heading": "결재 진행", "body": "출장 신청 승인 [approval:1]."}]}


# ---------------- 00 ----------------
def test_00_many_tools_records_errors_and_unseen_refs(env):
    m = load("00_many_tools_limit")
    assert len(m.TOOLS) == 12
    llm = ScriptedChatModel(responses=[
        tool_call("gw_list_mails", {"month": "2026-10"}),
        tool_call("gw_get_mails", {"month": "2026-10"}),           # 없는 도구
        tool_call("save_monthly_report", {"title": "10월", "sections": [
            {"heading": "업무", "body": "킥오프 [mail:1]. 지어낸 것 [mail:999]."}]}),
        "저장했습니다."])
    state = m.build_graph(llm).invoke(m.inputs())
    r = m.analyze(state)
    assert r["unknown_tools"] == ["gw_get_mails"] and r["tool_errors"]
    assert r["saved"] and r["cited_not_seen"] == ["mail:999"] and r["seen_refs"] > 10
    assert list(env.glob("s3_monthly_*.docx"))


# ---------------- 01 ----------------
def test_01_supervisor_routes_agents_then_writer(env):
    m = load("01_supervisor_worker")
    llm = ScriptedChatModel(responses=[brain] * 4, structured=[
        {"next": "mail_agent", "task": "메일 정리"}, {"next": "approval_agent", "task": "결재 정리"},
        {"next": "writer", "task": "작성"}, REPORT])
    state = m.build_graph(llm).invoke({"request": m.REQUEST})
    assert [r["agent"] for r in state["runs"]] == ["mail_agent", "approval_agent"]
    assert state["runs"][0]["tools"] == ["gw_list_mails"] and state["seen"]["mail_agent"]
    assert state["problems"] == [] and Path(state["saved_path"]).exists()


def test_01_turn_limit_forces_writer(env):
    m = load("01_supervisor_worker")
    llm = ScriptedChatModel(responses=[brain] * 2 * m.MAX_TURNS, structured=[
        *[{"next": "mail_agent", "task": "또 메일"}] * (m.MAX_TURNS + 1), REPORT])
    state = m.build_graph(llm).invoke({"request": m.REQUEST}, {"recursion_limit": 100})
    assert state["decisions"][-1]["forced"] and len(state["runs"]) == m.MAX_TURNS


# ---------------- 02 ----------------
def test_02_handoff_round_trip(env):
    m = load("02_handoff")
    llm = ScriptedChatModel(responses=[
        tool_call("transfer_to_collector", {"what": "10월 메일"}),
        tool_call("gw_list_mails", {"month": "2026-10"}),
        tool_call("transfer_to_writer", {"summary": "- 킥오프 [mail:1]"}),
        tool_call("save_monthly_report", {"title": "10월", "sections": [{"heading": "업무", "body": "킥오프 [mail:1]."}]}),
        "저장했습니다."])
    state = m.build_graph(llm).invoke(m.inputs())
    r = m.analyze(state)
    assert [h["to"] for h in r["handoffs"]] == ["collector", "writer"]
    assert r["calls"][0] == ("writer", "transfer_to_collector") and r["calls"][1] == ("collector", "gw_list_mails")
    assert r["saved"] and r["cited_not_seen"] == [] and r["ended_by"] == "writer"


def test_02_writer_calling_collector_tool_is_rejected(env):
    m = load("02_handoff")
    llm = ScriptedChatModel(responses=[tool_call("gw_list_mails", {"month": "2026-10"}), "못 하겠습니다."])
    r = m.analyze(m.build_graph(llm).invoke(m.inputs()))
    assert r["tool_errors"] and not r["saved"]


# ---------------- 03 ----------------
def test_03_teams_pass_up_only_results(env):
    m = load("03_hierarchical")
    llm = ScriptedChatModel(responses=[brain] * 2, structured=[
        {"next": "collect_team", "reason": "자료 먼저"}, {"next": "mail_agent", "task": "메일"}, {"next": "DONE", "task": ""},
        {"next": "write_team", "reason": "작성"}, REPORT, REPORT,      # 검토에서 걸려 한 번 더 씀
        {"next": "FINISH", "reason": "끝"}])
    state = m.build_graph(llm).invoke({"request": m.REQUEST})
    assert "messages" not in state                         # 팀 안의 대화는 위로 올라오지 않는다
    assert list(state["notes"]) == ["mail_agent"] and "mail:1" in state["known"]
    assert [t["team"] for t in state["team_log"]] == ["collect", "write"]
    rep = state["report"]
    assert any("approval:1" in p for p in rep["problems"])  # 수집 팀이 결재를 안 봤으니 결재 근거는 확인 안 됨
    assert rep["rounds"] == 2
    assert Path(rep["saved_path"]).exists()


# ---------------- 04 ----------------
@pytest.mark.parametrize("parallel", [True, False])
def test_04_collect_then_sequential_sections(env, parallel):
    m = load("04_sequential_parallel")
    llm = ScriptedChatModel(responses=[brain] * 12)
    state = m.build_graph(llm, parallel=parallel).invoke({"t0": time.perf_counter()})
    assert [s["heading"] for s in state["sections"]] == m.SECTIONS
    assert set(state["notes"]) == {"mail_agent", "schedule_agent", "approval_agent"}
    starts = {t["node"]: t["start"] for t in state["timings"]}
    assert starts["write_1"] >= starts["write_0"] and Path(state["saved_path"]).exists()


# ---------------- 05 ----------------
def test_05_shared_vs_result(env):
    m = load("05_shared_state")
    out = {}
    for shared in (True, False):
        llm = ScriptedChatModel(responses=[brain] * 6, structured=[REPORT])
        out[shared] = m.build_graph(llm, shared=shared).invoke({"messages": [], "notes": {}, "seen": [], "agent_inputs": []})
    sh, rs = out[True], out[False]
    assert sh["writer"]["messages_in"] > rs["writer"]["messages_in"] == 1
    assert [a["messages_in"] for a in sh["agent_inputs"]] == sorted(a["messages_in"] for a in sh["agent_inputs"])
    assert len(rs["notes"]) == 3 and rs["writer"]["problems"] == []
