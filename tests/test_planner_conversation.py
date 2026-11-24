"""서비스 보강: 단계 계획(Planner–Executor)과 대화 이어가기. 가짜 모델, GPU 없이."""

import json
import uuid

import pytest

from chat_ui.agent import assistant, toolcall

PLAN2 = json.dumps({"steps": [
    {"tool": "create_event", "goal": "12월 3일 8시 30분 제주도 워크샵 일정 만들기"},
    {"tool": "submit_approval", "goal": "1단계에서 만든 일정 내용으로 팀장님 결재선에 결재 올리기"}]}, ensure_ascii=False)


def _tools(log):
    ev = toolcall.Tool("create_event", "일정을 만든다", {"type": "object", "properties": {"title": {"type": "string"}}},
                       lambda **kw: log.append(("create_event", kw)) or '{"created": "event:17"}', writes=True)
    ap = toolcall.Tool("submit_approval", "결재를 올린다", {"type": "object", "properties": {"title": {"type": "string"}}},
                       lambda **kw: log.append(("submit_approval", kw)) or '{"submitted": "approval:7"}', writes=True)
    rd = toolcall.Tool("find_events", "일정을 찾는다", {"type": "object", "properties": {}}, lambda **kw: "[]")
    return [rd, ev, ap]


def test_plan_of_two_steps_is_run_by_code_one_tool_at_a_time():
    log, events = [], []
    llm = toolcall.ScriptedRaw([
        PLAN2,
        '<function=create_event>{"title": "제주도 워크샵"}</function>', "일정을 만들었습니다.",
        lambda p: ('<function=submit_approval>{"title": "제주도 워크샵 참석"}</function>' if "event:17" in p else "모름"),
        "결재를 올렸습니다.",
        "제주도 워크샵 일정을 만들고 [event:17] 팀장님께 결재를 올렸습니다 [approval:7]."])
    r = assistant.run("12월 3일 8시 30분 제주도 워크샵 일정 추가하고 그 내용으로 팀장님께 결재 올려 줘", llm, events.append,
                      tool_list=_tools(log), approve=lambda t, a: ("approve", a, ""), use_planner=True)
    assert [name for name, _ in log] == ["create_event", "submit_approval"]
    assert r.stop_reason == "plan_done" and "approval:7" in r.answer
    plan = next(e for e in events if e["type"] == "plan")
    assert [s["tool"] for s in plan["plan_steps"]] == ["create_event", "submit_approval"]
    # 2단계 프롬프트: 그 단계의 도구 하나만 보이고, 1단계 결과가 들어 있다
    step2_prompt = llm.prompts[3]
    assert "Use the function 'submit_approval'" in step2_prompt and "Use the function 'create_event'" not in step2_prompt
    assert "event:17" in step2_prompt
    assert [e["i"] for e in events if e["type"] == "step"] == [1, 2]


def test_step_that_only_talks_is_asked_once_more():
    log, events = [], []
    llm = toolcall.ScriptedRaw([
        PLAN2,
        "일정을 만들겠습니다. 잠시만요.",                                   # 말로만 함
        '<function=create_event>{"title": "제주도 워크샵"}</function>', "완료",  # 다시 시키자 부름
        '<function=submit_approval>{"title": "참석"}</function>', "완료",
        "끝났습니다."])
    r = assistant.run("일정 만들고 결재 올려 줘", llm, events.append, tool_list=_tools(log),
                      approve=lambda t, a: ("approve", a, ""), use_planner=True)
    assert [name for name, _ in log] == ["create_event", "submit_approval"] and r.stop_reason == "plan_done"
    assert "지금 바로 create_event 도구를 부르세요" in llm.prompts[2]


def test_rejected_step_stops_the_plan():
    log = []
    llm = toolcall.ScriptedRaw([PLAN2, '<function=create_event>{"title": "x"}</function>', "끝",
                                "일정을 만들지 않았습니다."])
    r = assistant.run("일정 만들고 결재 올려 줘", llm, lambda e: None, tool_list=_tools(log),
                      approve=lambda t, a: ("reject", a, "날짜가 틀렸어"), use_planner=True)
    assert log == [] and r.stop_reason == "plan_stopped"
    assert "멈춘 이유" in llm.prompts[-1] and "날짜가 틀렸어" in llm.prompts[-1]


def test_one_step_plan_falls_back_to_the_normal_loop():
    log = []
    llm = toolcall.ScriptedRaw([json.dumps({"steps": [{"tool": "find_events", "goal": "일정 찾기"}]}),
                                '<function=find_events>{}</function>', "일정이 없습니다."])
    r = assistant.run("12월 일정 있어?", llm, lambda e: None, tool_list=_tools(log), use_planner=True)
    assert r.stop_reason == "answered" and r.tool_calls == 1


@pytest.fixture()
def temp_runlog():
    from chat_ui.agent import runlog
    from infra.groupware import db as gw_db
    schema = f"agent_test_{uuid.uuid4().hex[:10]}"
    log = runlog.RunLog(runlog.DSN, schema)
    runlog.set_default(log)
    yield log
    runlog.set_default(None)
    log.close()
    gw_db.drop_schema(runlog.DSN, schema)


def test_conversation_carries_previous_turn_and_its_actions(gw_env, temp_runlog):
    from fastapi.testclient import TestClient

    from chat_ui import runner
    from chat_ui.server import create
    from tests.test_chat_ui import wait

    llm = toolcall.ScriptedRaw([
        '<function=gw_list_events>{"month": "2026-10"}</function>', "10월 일정은 12건입니다.",
        lambda p: "앞에서 본 10월 일정 이야기군요." if "10월 일정 보여 줘" in p and "gw_list_events" in p else "모름"])
    runner.set_raw_llm(llm)
    try:
        with TestClient(create(connect_msdf=False)) as c:
            conv = "conv-test"
            j1 = c.post("/chat/api/run", json={"key": "assistant", "text": "10월 일정 보여 줘", "stage": "1",
                                               "conversation": conv}).json()
            wait(c, j1["id"])
            j2 = c.post("/chat/api/run", json={"key": "assistant", "text": "방금 그거 다시 말해 줘", "stage": "1",
                                               "conversation": conv}).json()
            v2 = wait(c, j2["id"])
    finally:
        runner.set_raw_llm(None)
    assert v2["result"]["final_answer"] == "앞에서 본 10월 일정 이야기군요."
    assert "(이 턴에 실행한 도구: gw_list_events" in llm.prompts[-1]
    assert temp_runlog._get().one("SELECT count(*) AS n FROM runs WHERE conversation_id='conv-test'")["n"] == 2
    runner.CONVERSATIONS.pop("conv-test", None)


def test_failed_read_step_does_not_stop_and_record_is_appended():
    log = []
    bad = toolcall.Tool("read_approval", "결재 하나", {"type": "object", "properties": {"approval_id": {"type": "integer"}}},
                        lambda **kw: "오류(서버가 isError로 돌려줌): 결재 문서가 없습니다")
    plan3 = json.dumps({"steps": [{"tool": "read_approval", "goal": "결재 읽기"},
                                  {"tool": "submit_approval", "goal": "결재 올리기"}]})
    llm = toolcall.ScriptedRaw([plan3, '<function=read_approval>{"approval_id": 1}</function>', "없음",
                                '<function=submit_approval>{"title": "워크샵"}</function>', "완료",
                                "결재를 올렸습니다 [approval:99]."])
    r = assistant.run("워크샵 결재 올려 줘", llm, lambda e: None, tool_list=[*_tools(log), bad],
                      approve=lambda t, a: ("approve", a, ""), use_planner=True)
    assert [n for n, _ in log] == ["submit_approval"] and r.stop_reason == "plan_done"
    assert "[실행 기록]" in r.answer and "✗ 1. read_approval" in r.answer and "submitted: approval:7" in r.answer


def test_near_miss_call_is_sent_back_and_false_claims_are_flagged():
    log, events = [], []
    llm = toolcall.ScriptedRaw([
        '결재를 올립니다. [submit_approval>{"title": "워크샵"}</submit_approval> 결재를 올렸습니다.',
        '<function=submit_approval>{"title": "워크샵"}</function>', "결재를 올렸습니다 [approval:7]."])
    r = assistant.run("워크샵 결재 올려 줘", llm, events.append, tool_list=_tools(log),
                      approve=lambda t, a: ("approve", a, ""))
    assert [n for n, _ in log] == ["submit_approval"]                     # 형식 오류를 돌려받고 제대로 다시 불렀다
    assert "형식이 틀려 실행되지 않았습니다" in llm.prompts[1]
    # 쓰기를 하지 않았는데 했다고 답하면 코드가 덧붙인다
    fake = assistant.guard_write_claims("결재를 올렸습니다.", [], _tools([]))
    assert "실제로 실행된 쓰기 작업" in fake
    real = assistant.guard_write_claims("결재를 올렸습니다.", events, _tools([]))
    assert "⚠" not in real


def test_single_write_step_is_also_run_by_code():
    log = []
    plan1 = json.dumps({"steps": [{"tool": "submit_approval", "goal": "앞 턴에서 만든 일정으로 결재 올리기"}]})
    llm = toolcall.ScriptedRaw([plan1, '<function=submit_approval>{"title": "워크샵"}</function>', "완료",
                                "결재를 올렸습니다."])
    r = assistant.run("그 내용으로 결재 올려 줘", llm, lambda e: None, tool_list=_tools(log),
                      approve=lambda t, a: ("approve", a, ""), use_planner=True)
    assert [n for n, _ in log] == ["submit_approval"] and r.stop_reason == "plan_done"
