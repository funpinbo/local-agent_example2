"""4편 2장 MCP 호스트. 테스트용 stdio 서버(tests/mcp_fake_server.py)에 실제로 붙는다. GPU·네트워크 없이."""

import textwrap

import pytest

from chat_ui.agent import assistant, mcp_host, toolcall

FAKE = "${ROOT}/tests/mcp_fake_server.py"


@pytest.fixture()
def host(tmp_path):
    cfg = tmp_path / "servers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        servers:
          office:
            transport: stdio
            command: ${{PYTHON}}
            args: ["{FAKE}"]
            read_only: true
          secret:
            transport: stdio
            command: ${{PYTHON}}
            args: ["{FAKE}"]
            env: {{TOKEN: "${{SOME_TOKEN_THAT_IS_NOT_SET}}"}}
    """), encoding="utf-8")
    h = mcp_host.McpHost(cfg, env={"SOME_TOKEN_THAT_IS_NOT_SET": ""}, connect_timeout=60)
    yield h
    h.close()


def test_connect_lists_tools_and_drops_write_tools(host):
    st = {s["name"]: s for s in host.connect()}
    office = st["office"]
    assert office["status"] == "ready" and office["protocol"]
    assert {t["name"]: t["used"] for t in office["offered"]} == {"add": True, "team_of": True, "send_note": False}
    assert st["secret"]["status"] == "needs_env" and st["secret"]["missing_env"] == ["SOME_TOKEN_THAT_IS_NOT_SET"]
    # 연결할 때 오간 메시지(실행 밖)는 boot_log에
    methods = [m["method"] for m in host.boot_log if m["dir"] == "send"]
    assert "tools/list" in methods


def test_call_goes_over_json_rpc_and_is_tapped(host):
    host.connect()
    seen = []
    host.add_listener(seen.append)
    try:
        add = next(t for t in host.tools() if t.name == "add")
        assert add.source == "mcp:office" and add.parameters["required"] == ["a", "b"]
        assert add.run({"a": 2, "b": 3}) == "5"
    finally:
        host.remove_listener(seen.append)
    sent = next(m for m in seen if m["dir"] == "send")
    assert sent["method"] == "tools/call" and sent["msg"]["params"]["name"] == "add"
    assert sent["msg"]["params"]["arguments"] == {"a": 2, "b": 3}
    got = next(m for m in seen if m["dir"] == "recv" and m["rpc_id"] == sent["rpc_id"])
    assert got["msg"]["result"]["content"][0]["text"] == "5"


def test_assistant_mixes_local_and_mcp_tools_in_one_prompt(host):
    host.connect()
    ts = assistant.tools(host)
    sources = {t.name: t.source for t in ts}
    assert sources["gw_search_mails"] == "local" and sources["team_of"] == "mcp:office"
    prompt = toolcall.render(assistant.messages_for("태양 님 팀이 어디야?"), ts)
    assert "Use the function 'team_of' to '사용자 아이디로 팀 이름을 알려 준다.'" in prompt
    events = []
    llm = toolcall.ScriptedRaw(['<function=team_of>{"user": "taeyang"}</function>', "보안팀입니다."])
    r = assistant.run("태양 님 팀이 어디야?", llm, events.append, host=host)
    assert r.answer == "보안팀입니다."
    tool_ev = next(e for e in events if e["type"] == "tool")
    assert tool_ev["content"] == "보안팀" and tool_ev["ok"]


def test_panel_run_records_mcp_messages(host, gw_env, monkeypatch):
    import uuid

    from fastapi.testclient import TestClient

    from chat_ui import runner
    from chat_ui.agent import runlog
    from chat_ui.server import create
    from infra.groupware import db as gw_db
    from tests.test_chat_ui import wait

    schema = f"agent_test_{uuid.uuid4().hex[:10]}"
    log = runlog.RunLog(runlog.DSN, schema)
    runlog.set_default(log)
    runner.set_mcp_host(host)
    runner.set_raw_llm(toolcall.ScriptedRaw(['<function=add>{"a": 40, "b": 2}</function>', "42입니다."]))
    try:
        with TestClient(create(connect_msdf=False)) as c:
            job = c.post("/chat/api/run", json={"key": "assistant", "text": "40 더하기 2는?", "skills": "off", "planner": "off"}).json()
            v = wait(c, job["id"])
            status = c.get("/chat/api/mcp").json()
        assert v["status"] == "done", v["error"]
        types = [e["type"] for e in v["events"]]
        assert "mcp_status" in types and types.count("mcp") >= 2
        assert {s["name"]: s["status"] for s in status["servers"]}["office"] == "ready"
        rows = log._get().all("SELECT direction, method FROM mcp_messages WHERE run_id=%s ORDER BY id", (job["id"],))
        assert ("send", "tools/call") in [(r["direction"], r["method"]) for r in rows]
    finally:
        runner.set_raw_llm(None)
        runner.set_mcp_host(None, disabled=True)
        runlog.set_default(None)
        log.close()
        gw_db.drop_schema(runlog.DSN, schema)


def test_verified_read_overrides_server_hint(tmp_path):
    cfg = tmp_path / "servers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        servers:
          office:
            transport: stdio
            command: ${{PYTHON}}
            args: ["{FAKE}"]
            read_only: true
            verified_read: [send_note]
            tools: [send_note, add]
    """), encoding="utf-8")
    h = mcp_host.McpHost(cfg, connect_timeout=60)
    try:
        office = h.connect()[0]
        tools = {t["name"]: t for t in office["offered"]}
        assert tools["send_note"]["read_only_hint"] is False and tools["send_note"]["used"] is True
        assert tools["team_of"]["used"] is False                      # 허용 목록 밖
        assert sorted(office["used"]) == ["add", "send_note"]
    finally:
        h.close()
