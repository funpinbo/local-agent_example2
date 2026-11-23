"""4편 3장: 직접 만든 그룹웨어 MCP 서버 + 쓰기 도구 사람 승인. 가짜 모델, GPU 없이."""

import asyncio
import json
import socket
import textwrap
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client

from chat_ui.agent import mcp_host, toolcall
from chat_ui.mcp_servers.groupware import server as gw_mcp

U = {"local.office/user": "jaehwi"}


def _call(name, args, meta=U):
    async def go():
        async with Client(gw_mcp.server) as c:          # 같은 프로세스 안에서 붙는다
            return await c.call_tool(name, args, meta=meta)
    return asyncio.run(go())


@pytest.fixture()
def gw(gw_env, monkeypatch):
    monkeypatch.setenv("GROUPWARE_URL", gw_env)
    gw_mcp._people_cache = None
    yield gw_env
    gw_mcp._people_cache = None


def test_find_approvals_crosses_months_and_gives_reason(gw):
    r = json.loads(_call("find_approvals", {"status": "rejected"}).content[0].text)
    assert r["count"] == 1
    a = r["approvals"][0]
    assert a["ref"] == "approval:4" and a["last_decision"]["comment"].startswith("개인정보")
    assert a["line"] == ["박지영(AI플랫폼팀)", "오태양(보안팀)"]


def test_find_mails_range_is_inclusive_and_names_people(gw):
    r = json.loads(_call("find_mails", {"query": "OOM", "since": "2026-10-08", "until": "2026-10-08"}).content[0].text)
    assert r["count"] >= 2 and all(m["at"].startswith("2026-10-08") for m in r["mails"])
    assert r["mails"][0]["from"].endswith("(AI플랫폼팀)")


def test_errors_reach_the_model_as_text(gw):
    r = _call("find_mails", {"query": "OOM"}, meta=None)
    assert r.is_error and "local.office/user" in r.content[0].text
    r = _call("send_mail", {"to": ["없는사람"], "subject": "x", "body": "y"})
    assert r.is_error and "'없는사람'에 해당하는 사람이 없습니다" in r.content[0].text


def test_send_mail_accepts_names(gw):
    r = json.loads(_call("send_mail", {"to": ["박지영"], "subject": "테스트", "body": "본문"}).content[0].text)
    mid = int(r["sent"].split(":")[1])
    m = json.loads(_call("read_mail", {"mail_id": mid}, meta={"local.office/user": "jiyoung"}).content[0].text)
    assert m["from"] == "이재휘(AI플랫폼팀)" and m["subject"] == "테스트"


def test_http_app_requires_service_key():
    with TestClient(gw_mcp.app(key="k1")) as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer k1"}).status_code != 401


def _writer(log):
    return toolcall.Tool("send_note", "메모를 보낸다", {"type": "object", "properties": {"to": {"type": "string"}}},
                         lambda to: log.append(to) or f"{to}에게 보냄", writes=True)


def test_write_tool_waits_for_human_and_can_be_rejected_or_edited():
    sent = []
    llm = toolcall.ScriptedRaw(['<function=send_note>{"to": "지영"}</function>', "보내지 않았습니다."])
    r = toolcall.run_loop(llm, [{"role": "user", "content": "?"}], [_writer(sent)],
                          approve=lambda t, a: ("reject", a, "아직 보내지 마"))
    assert sent == [] and "거절했습니다. 의견: 아직 보내지 마" in llm.prompts[1]
    llm = toolcall.ScriptedRaw(['<function=send_note>{"to": "지영"}</function>', "보냈습니다."])
    events = []
    toolcall.run_loop(llm, [{"role": "user", "content": "?"}], [_writer(sent)], emit=events.append,
                      approve=lambda t, a: ("approve", {"to": "동호"}, ""))
    assert sent == ["동호"]                                         # 사람이 고친 인자로 실행
    ap = next(e for e in events if e["type"] == "approval")
    assert ap["args"] == {"to": "지영"} and ap["final_args"] == {"to": "동호"}
    # 승인 절차가 없으면 쓰기 도구는 실행하지 않는다
    llm = toolcall.ScriptedRaw(['<function=send_note>{"to": "지영"}</function>', "끝"])
    toolcall.run_loop(llm, [{"role": "user", "content": "?"}], [_writer(sent)])
    assert sent == ["동호"]


@pytest.fixture()
def gw_mcp_http(gw):
    """그룹웨어 MCP 서버를 HTTP(임의 포트, 서비스 키)로 띄운다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(gw_mcp.app(key="test-key"), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    th.join(5)


def test_panel_send_mail_after_approval_over_http(gw_mcp_http, gw, tmp_path):
    from chat_ui import runner
    from chat_ui.server import create
    from tests.test_chat_ui import wait

    cfg = tmp_path / "servers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        servers:
          groupware:
            transport: http
            url: {gw_mcp_http}
            headers: {{Authorization: "Bearer ${{K}}"}}
            pass_user: true
    """), encoding="utf-8")
    host = mcp_host.McpHost(cfg, env={"K": "test-key"}, connect_timeout=30)
    runner.set_mcp_host(host)
    runner.set_raw_llm(toolcall.ScriptedRaw([
        '<function=send_mail>{"to": ["박지영"], "subject": "회의 요청", "body": "내일 10시 어떠세요?"}</function>',
        "보냈습니다 [mail:32]."]))
    try:
        with TestClient(create(connect_msdf=False)) as c:
            job = c.post("/chat/api/run", json={"key": "assistant", "text": "지영 팀장님께 회의 요청 메일 보내 줘",
                                                "user": "haeun"}).json()
            v = wait(c, job["id"], until=("waiting", "done", "error"))
            assert v["status"] == "waiting", v["error"]
            it = v["interrupt"]
            assert it["kind"] == "tool" and it["tool"] == "send_mail" and it["server"] == "mcp:groupware"
            c.post(f"/chat/api/run/{job['id']}/resume", json={"action": "edit", "args": {
                "to": ["박지영"], "subject": "회의 요청 (수정)", "body": "내일 10시 어떠세요?"}})
            v = wait(c, job["id"], until=("done", "error"))
        assert v["status"] == "done", v["error"]
        tools = [e for e in v["events"] if e["type"] == "tool"]
        assert tools[0]["ok"] and json.loads(tools[0]["content"])["sent"].startswith("mail:")
        names = next(e for e in v["events"] if e["type"] == "tools")["names"]
        assert "gw_list_mails" not in names and "find_mails" in names     # 비서 코드 안의 그룹웨어 도구는 빠졌다
    finally:
        runner.set_raw_llm(None)
        host.close()
    # 메일은 실행한 사용자(haeun) 이름으로, 사람이 고친 제목으로 갔다
    import httpx
    inbox = httpx.get(f"{gw}/api/mails", headers={"X-User": "jiyoung"}).json()
    m = inbox[-1]
    assert m["sender"] == "haeun" and m["subject"] == "회의 요청 (수정)"


def test_find_approvals_query_matches_people_and_teams(gw):
    r = json.loads(_call("find_approvals", {"query": "보안팀", "status": "rejected"}).content[0].text)
    assert [a["ref"] for a in r["approvals"]] == ["approval:4"]
    r = json.loads(_call("find_approvals", {"query": "60일 이하"}).content[0].text)       # 결재 의견에서
    assert "approval:4" in [a["ref"] for a in r["approvals"]]


def test_stage_option_reproduces_chapter_tool_sets(gw_mcp_http, gw, tmp_path):
    from chat_ui import runner
    from chat_ui.server import create
    from tests.test_chat_ui import wait

    cfg = tmp_path / "servers.yaml"
    cfg.write_text(textwrap.dedent(f"""
        servers:
          groupware:
            transport: http
            url: {gw_mcp_http}
            headers: {{Authorization: "Bearer ${{K}}"}}
            pass_user: true
    """), encoding="utf-8")
    host = mcp_host.McpHost(cfg, env={"K": "test-key"}, connect_timeout=30)
    runner.set_mcp_host(host)
    seen = {}
    try:
        with TestClient(create(connect_msdf=False)) as c:
            for stage in ["1", "2", "3", "5"]:
                runner.set_raw_llm(toolcall.ScriptedRaw(["끝"]))
                job = c.post("/chat/api/run", json={"key": "assistant", "text": "?", "stage": stage}).json()
                v = wait(c, job["id"])
                seen[stage] = next(e for e in v["events"] if e["type"] == "tools")["sources"]
    finally:
        runner.set_raw_llm(None)
        host.close()
    srcs = {k: set(v.values()) for k, v in seen.items()}
    assert srcs["1"] == {"local"} and len(seen["1"]) == 11            # 3편 도구 11개
    assert srcs["2"] == {"local"}                                     # groupware 서버만 있는 테스트라 2장은 local만
    assert "mcp:groupware" in srcs["3"] and "gw_list_mails" not in seen["3"]
    assert "skill" in srcs["5"]
