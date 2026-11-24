"""4편 1장 Tool Calling 층. 가짜 모델(ScriptedRaw)로 GPU 없이."""

import json
import uuid

import pytest

from chat_ui.agent import assistant, runlog, toolcall
from infra.groupware import db as gw_db


def _tool(name="get_weather", fn=lambda city: f"{city}: 맑음"):
    return toolcall.Tool(name, "도시의 현재 날씨를 알려 준다",
                         {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, fn)


def test_render_puts_tool_schema_into_system_block_without_bos():
    p = toolcall.render([{"role": "user", "content": "대전 날씨?"}], [_tool()])
    assert not p.startswith("<|begin_of_text|>")                     # Ollama가 BOS를 붙인다
    assert "Use the function 'get_weather' to '도시의 현재 날씨를 알려 준다'" in p
    assert '"required": ["city"]' in p and "Only call one function at a time" in p
    assert p.endswith("<|start_header_id|>assistant<|end_header_id|>\n\n")
    assert "You have access" not in toolcall.render([{"role": "user", "content": "안녕"}], [])


def test_loop_runs_tool_and_feeds_result_back_as_ipython():
    llm = toolcall.ScriptedRaw(['<function=get_weather>{"city": "대전"}</function>', "대전은 맑습니다."])
    events = []
    r = toolcall.run_loop(llm, [{"role": "user", "content": "대전 날씨?"}], [_tool()], emit=events.append)
    assert r.answer == "대전은 맑습니다." and r.stop_reason == "answered" and (r.llm_calls, r.tool_calls) == (2, 1)
    assert [e["type"] for e in events] == ["llm_raw", "parsed", "tool", "llm_raw", "parsed"]
    second = events[3]
    # 두 번째 프롬프트 = 첫 프롬프트 + 모델이 쓴 글자 그대로 + <|eom_id|> + ipython 역할의 결과 + 다음 assistant 머리
    assert second["prompt_delta"] == ('<function=get_weather>{"city": "대전"}</function><|eom_id|>'
                                      "<|start_header_id|>ipython<|end_header_id|>\n\n대전: 맑음<|eot_id|>"
                                      "<|start_header_id|>assistant<|end_header_id|>\n\n")


def test_wrong_calls_are_returned_to_the_model_not_raised():
    llm = toolcall.ScriptedRaw([
        '<function=get_wether>{"city": "대전"}</function>',          # 없는 도구
        '<function=get_weather>{"city": 대전}</function>',            # 깨진 JSON
        '<function=get_weather>{"town": "대전"}</function>',          # 인자 이름이 틀림
        "모르겠습니다."])
    events = []
    r = toolcall.run_loop(llm, [{"role": "user", "content": "대전 날씨?"}], [_tool()], emit=events.append)
    results = [e["content"] for e in events if e["type"] == "tool"]
    assert "'get_wether'라는 도구는 없습니다" in results[0]
    assert "형식이 잘못되었습니다" in results[1]
    assert "인자가 맞지 않습니다" in results[2]
    assert r.stop_reason == "answered"
    assert "오류: '" in llm.prompts[1]                                 # 오류 글이 다음 프롬프트에 들어갔다


def test_code_stops_repeated_calls_and_too_many_llm_calls():
    same = '<function=get_weather>{"city": "대전"}</function>'
    r = toolcall.run_loop(toolcall.ScriptedRaw([same] * 5), [{"role": "user", "content": "?"}], [_tool()])
    assert r.stop_reason == "repeated_call" and r.tool_calls == 2
    many = [f'<function=get_weather>{{"city": "{c}"}}</function>' for c in "가나다라마바"]
    r = toolcall.run_loop(toolcall.ScriptedRaw(many), [{"role": "user", "content": "?"}], [_tool()], max_llm_calls=3)
    assert r.stop_reason == "max_llm_calls" and r.llm_calls == 3


def test_langchain_tools_become_json_schema():
    names = [t.name for t in assistant.tools()]
    assert len(names) == 11 and "gw_search_mails" in names and "doc_search" in names
    s = next(t for t in assistant.tools() if t.name == "gw_search_mails").schema()["function"]
    assert s["parameters"]["required"] == ["keyword", "month"]


@pytest.fixture()
def temp_runlog():
    schema = f"agent_test_{uuid.uuid4().hex[:10]}"
    log = runlog.RunLog(runlog.DSN, schema)
    runlog.set_default(log)
    yield log
    runlog.set_default(None)
    log.close()
    gw_db.drop_schema(runlog.DSN, schema)


def test_runlog_keeps_prompt_and_output_verbatim(temp_runlog):
    llm = toolcall.ScriptedRaw(['<function=get_weather>{"city": "대전"}</function>', "맑음 [w:1]"])
    temp_runlog.start("r1", "assistant", "jaehwi", "대전 날씨?", "scripted")
    r = toolcall.run_loop(llm, [{"role": "user", "content": "대전 날씨?"}], [_tool()],
                          emit=lambda e: temp_runlog.event("r1", e))
    temp_runlog.finish("r1", "done", r.answer, r.stop_reason)
    s = temp_runlog._get()
    calls = s.all("SELECT seq, prompt, output FROM llm_calls WHERE run_id='r1' ORDER BY seq")
    assert [c["seq"] for c in calls] == [1, 2] and calls[0]["output"].startswith("<function=get_weather>")
    assert calls[1]["prompt"] == llm.prompts[1]
    tool = s.one("SELECT name, args, ok FROM tool_calls WHERE run_id='r1'")
    assert tool == {"name": "get_weather", "args": {"city": "대전"}, "ok": True}
    assert s.one("SELECT stop_reason FROM runs WHERE id='r1'")["stop_reason"] == "answered"


def test_panel_runs_assistant_with_raw_events(gw_env, temp_runlog):
    from fastapi.testclient import TestClient

    from chat_ui import runner
    from chat_ui.server import create
    from tests.test_chat_ui import wait

    runner.set_raw_llm(toolcall.ScriptedRaw([
        '<function=gw_search_mails>{"keyword": "OOM", "month": "2026-10"}</function>',
        lambda p: "2호기가 OOM으로 재시작했습니다 [mail:6]." if "mail:6" in p else "모르겠습니다."]))
    try:
        with TestClient(create(connect_msdf=False)) as c:
            job = c.post("/chat/api/run", json={"key": "assistant", "text": "10월에 OOM 난 적 있어?", "skills": "off", "planner": "off"}).json()
            v = wait(c, job["id"])
    finally:
        runner.set_raw_llm(None)
    assert v["status"] == "done", v["error"]
    assert v["result"]["final_answer"].endswith("[mail:6].") and v["result"]["tool_calls"] == 1
    types = [e["type"] for e in v["events"]]
    assert types.index("tools") < types.index("llm_raw") and "parsed" in types and "tool" in types
    raw = next(e for e in v["events"] if e["type"] == "llm_raw")
    assert "Use the function 'gw_search_mails'" in raw["prompt"]
    assert temp_runlog._get().one("SELECT count(*) AS n FROM llm_calls WHERE run_id=%s", (job["id"],))["n"] == 2
