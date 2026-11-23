"""1단계 그래프의 연결과 흐름 검증 (가짜 LLM, GPU 없이)."""

import importlib.util
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from common import config
from common.fake_llm import ScriptedChatModel, tool_call

ROOT = Path(__file__).resolve().parents[1]
MEMO = (config.SAMPLE_DIR / "memo_weekly.txt").read_text(encoding="utf-8")
TRIP_MEMO = (config.SAMPLE_DIR / "memo_trip.txt").read_text(encoding="utf-8")
SHORT = (config.SAMPLE_DIR / "memo_short.txt").read_text(encoding="utf-8")

WEEKLY = {"period": "2026-11-09 ~ 2026-11-13", "done": "vLLM 새 버전 설치, 문서 검색 지연 대응",
          "issues": "기동 실패 → max-model-len 조정", "next_plan": "부하 테스트 실행", "notes": "구매팀 견적 독촉 요청"}
TRIP = {"destination": "판교 데이터센터", "period": "2026-11-05 ~ 11-06", "purpose": "코로케이션 후보 견학",
        "content": "랙당 전력 15kW, 공랭만 가능", "result": "현재 장비 기준 문제 없음",
        "follow_up": "이재휘 · 담당자·기한 미정 · 장비 목록 전달"}


def load(name: str):
    path = ROOT / "s1_single" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"s1_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # State의 타입 힌트(from __future__ annotations)를 풀 때 필요
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    return tmp_path


def tool_texts(state, name):
    return [m.content for m in state["messages"] if isinstance(m, ToolMessage) and m.name == name]


def fills(fields):
    return [tool_call("fill_field", {"field": k, "value": v}) for k, v in fields.items()]


# ---------------- 01 ----------------
def test_01_tool_loop_saves(out_dir):
    m = load("01_tool_calling")
    llm = ScriptedChatModel(responses=[
        tool_call("list_template_fields", {"template": "weekly"}),
        tool_call("save_report", {"template": "weekly", "fields": WEEKLY}),
        "저장했습니다.",
    ])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert "보고 기간" in tool_texts(state, "list_template_fields")[0]
    assert tool_texts(state, "save_report")[0].startswith("저장했습니다")
    assert list(out_dir.glob("weekly_*.docx"))
    assert state["messages"][-1].content == "저장했습니다."


def test_01_missing_field_rejected_then_fixed(out_dir):
    m = load("01_tool_calling")
    partial = {k: v for k, v in WEEKLY.items() if k != "next_plan"}
    llm = ScriptedChatModel(responses=[
        tool_call("save_report", {"template": "weekly", "fields": partial}),
        tool_call("save_report", {"template": "weekly", "fields": WEEKLY}),
        "저장했습니다.",
    ])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    first, second = tool_texts(state, "save_report")
    assert first.startswith("저장하지 않았습니다") and "next_plan" in first
    assert second.startswith("저장했습니다")


# ---------------- 02 ----------------
def test_02_fill_loop_draft_in_state(out_dir):
    m = load("02_react_loop")
    llm = ScriptedChatModel(responses=[
        tool_call("list_template_fields", {"template": "weekly"}),
        *fills(WEEKLY),
        tool_call("fill_field", {"field": "done", "value": WEEKLY["done"] + " (수정)"}),   # 같은 칸 덮어쓰기
        tool_call("show_draft"), tool_call("save_draft"), "저장했습니다.",
    ])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert state["draft"]["done"].endswith("(수정)")
    assert state["saved_path"].endswith(".docx")
    sizes = [c["messages"] for c in state["context"]]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]      # 루프가 돌수록 입력이 늘어난다


def test_02_unknown_field_and_early_save_rejected(out_dir):
    m = load("02_react_loop")
    llm = ScriptedChatModel(responses=[
        tool_call("fill_field", {"field": "budget", "value": "100만원"}),
        tool_call("save_draft"),
        *fills(WEEKLY), tool_call("save_draft"), "끝",
    ])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert "없는 칸" in tool_texts(state, "fill_field")[0]
    assert "budget" not in state["draft"]
    saves = tool_texts(state, "save_draft")
    assert saves[0].startswith("저장하지 않았습니다") and saves[1].startswith("저장했습니다")


# ---------------- 03 ----------------
def test_03_code_branch_skips_selection(out_dir):
    m = load("03_conditional_branch")
    llm = ScriptedChatModel(responses=[*fills(TRIP), tool_call("save_draft"), "저장했습니다."])
    state = m.build_graph(llm).invoke(m.inputs(TRIP_MEMO, "trip"))
    assert state["route"] == "code"
    assert not tool_texts(state, "select_template")
    first_llm_input = llm.calls[0]
    assert "[양식 안내]" in first_llm_input[-1].content and "출장비" in first_llm_input[-1].content
    assert state["saved_path"]


def test_03_llm_output_branch(out_dir):
    m = load("03_conditional_branch")
    llm = ScriptedChatModel(responses=[
        tool_call("select_template", {"template": "trip"}),
        *fills(TRIP), tool_call("save_draft"), "저장했습니다.",
    ])
    state = m.build_graph(llm).invoke(m.inputs(TRIP_MEMO, None))
    assert state["route"] == "llm" and state["template"] == "trip"
    # 양식 안내는 select 뒤, 두 번째 LLM 호출 직전에 들어간다
    assert "[양식 안내]" not in llm.calls[0][-1].content
    assert "누가 · 언제까지 · 무엇을" in llm.calls[1][-1].content
    assert state["saved_path"]


# ---------------- 04 ----------------
def test_04_retry_recovers(out_dir):
    m = load("04_retry_fallback_stop")
    broken = AIMessage(content="", invalid_tool_calls=[
        {"name": "fill_field", "args": None, "id": "x", "error": "fill_field: 인자 해석 실패", "type": "invalid_tool_call"}])
    llm = ScriptedChatModel(responses=[broken, *fills(WEEKLY), tool_call("save_draft"), "저장했습니다."])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert state["retries"] == 1 and state["outcome"] == "done"
    assert "인자를 읽지 못했습니다" in state["problem"]


def test_04_json_text_goes_to_fallback(out_dir):
    m = load("04_retry_fallback_stop")
    text = '{"name": "fill_field", "arguments": {"field": "done"}}'
    llm = ScriptedChatModel(responses=[text, text, text], structured=[WEEKLY])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert state["retries"] == 2 and state["outcome"] == "fallback"
    assert "글자로 썼습니다" in state["problem"]
    assert Path(state["saved_path"]).exists()


def test_04_repeat_stops(out_dir):
    m = load("04_retry_fallback_stop")
    llm = ScriptedChatModel(responses=[tool_call("show_draft")] * 3)
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"))
    assert state["outcome"] == "repeat" and state["steps"] == 3


def test_04_max_steps_stops(out_dir):
    m = load("04_retry_fallback_stop")
    llm = ScriptedChatModel(responses=[tool_call("fill_field", {"field": "done", "value": f"v{i}"}) for i in range(20)])
    state = m.build_graph(llm).invoke(m.inputs(MEMO, "weekly"), {"recursion_limit": 100})
    assert state["outcome"] == "max_steps" and state["steps"] == m.MAX_STEPS + 1


# ---------------- 05 ----------------
def test_05_self_check_misses_invented_date_then_fixed(out_dir):
    m = load("05_reflection")
    invented = {**WEEKLY, "period": "2026-11-09 ~ 2026-11-13", "next_plan": "11월 20일까지 부하 테스트 완료"}
    fixed = {**WEEKLY, "next_plan": "메모에 계획 없음"}
    llm = ScriptedChatModel(responses=[], structured=[
        invented, {"ok": True, "problems": []},     # 1회차: 자기 점검은 통과
        fixed, {"ok": True, "problems": []},        # 2회차
    ])
    state = m.build_graph(llm).invoke(m.inputs(SHORT, "weekly"))
    first, second = state["reviews"]
    assert first["missed_by_self"] and any("'20'" in p for p in first["code"])
    assert not second["code"] and state["outcome"] == "saved"
    assert Path(state["saved_path"]).exists()


def test_05_hand_over_after_two_revisions(out_dir):
    m = load("05_reflection")
    bad = {**WEEKLY, "notes": ""}                     # 빈 칸이 계속 남는다
    llm = ScriptedChatModel(responses=[], structured=[
        bad, {"ok": False, "problems": ["notes 비어 있음"]},
        bad, {"ok": False, "problems": ["notes 비어 있음"]},
        bad, {"ok": True, "problems": []},
    ])
    state = m.build_graph(llm).invoke(m.inputs(SHORT, "weekly"))
    assert state["outcome"] == "hand_over" and state["round"] == 3
    assert any("notes" in p for p in state["problems"])
    assert state["reviews"][-1]["missed_by_self"]
    assert not list(out_dir.glob("*.docx"))
