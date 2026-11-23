"""챗봇 화면(chat_ui)과 새 MSDF 결과물(memo_text, doc_ingest, gw_people) 검증. 가짜 LLM, GPU 없이."""

import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from chat_ui import runner, uploads
from chat_ui.server import create
from common import config, memo, monthly_report
from common.fake_llm import ScriptedChatModel, tool_call
from infra.msdf.services import agent, docs as docs_service


@pytest.fixture()
def chat(gw_env, tmp_path, monkeypatch):
    # 문서 저장소를 임시 폴더로 (사내 문서 6개 복사)
    root = tmp_path / "docs"
    shutil.copytree(config.SAMPLE_DIR / "docs" / "llm_server", root / "llm_server")
    for mod in (uploads, docs_service):
        monkeypatch.setattr(mod, "DOCS_ROOT", root)
    monkeypatch.setattr(uploads, "STORE", root / "_uploaded_files")
    monkeypatch.setattr(uploads, "STATE", root / "_uploaded_files" / "_state.json")
    monkeypatch.setattr(docs_service, "UPLOAD_STORE", root / "_uploaded_files")
    app = create(connect_msdf=False)
    with TestClient(app) as c:
        yield c
    runner.set_llm(None)


def wait(c, job_id, until=("done", "error", "waiting"), timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/chat/api/run/{job_id}").json()
        if v["status"] in until:
            return v
        time.sleep(0.05)
    raise AssertionError("작업이 끝나지 않음")


def test_new_msdf_orders(gw_env):
    m = memo.load(config.SAMPLE_DIR / "memo_short.txt")
    assert "이재휘" in m
    from common import gw_tools
    assert "jaehwi" in gw_tools.gw_people.invoke({})


def test_catalog_lists_all_patterns(chat):
    cat = chat.get("/chat/api/catalog").json()
    keys = [i["key"] for s in cat["stages"] for i in s["items"]]
    assert len(keys) == 24 and keys[0] == "assistant"        # 비서 1 + 챗봇 1 + s1 5 + s2 6 + s3 6 + s4 5
    assert {s["stage"] for s in cat["stages"]} == {"chat", "s1", "s2", "s3", "s4"}


def test_upload_goes_through_msdf_and_is_searchable(chat):
    events = []
    agent.add_listener(events.append)
    try:
        r = chat.post("/chat/api/uploads", files={"file": ("휴가규정.md", "# 휴가 규정\n\n## 연차\n연차는 15일이다.".encode())})
    finally:
        agent.remove_listener(events.append)
    assert r.status_code == 200 and r.json()["corpus"].startswith("uploads/")
    assert [e["order"] for e in events] == ["doc_ingest"]
    hits = agent.fetch("doc_search", {"corpus": uploads.corpus(), "q": "연차 휴가", "k": 2})
    assert hits[0]["source"].startswith("up_")
    assert chat.delete("/chat/api/uploads").json()["corpus"] is None


def test_chat_qa_shows_msdf_and_answer(chat):
    def answer(msgs):
        ev = msgs[-1].content.split("[근거]", 1)[1]
        cid = ev.split("[", 1)[1].split("]", 1)[0]
        return f"메모리 설정이 원인이다 [{cid}]."
    runner.set_llm(ScriptedChatModel(responses=[answer]))
    job = chat.post("/chat/api/run", json={"key": "chat_qa", "text": "OOM 원인은?"}).json()
    v = wait(chat, job["id"])
    assert v["status"] == "done" and v["result"]["problems"] == []
    kinds = [e["type"] for e in v["events"]]
    assert "msdf" in kinds and "ai" in kinds
    msdf_ev = next(e for e in v["events"] if e["type"] == "msdf")
    assert msdf_ev["order"] == "doc_search" and {"load", "chunk", "index"} <= set(msdf_ev["steps"] + msdf_ev["cached"])


def test_single_agent_run_shows_tool_calls(chat):
    runner.set_llm(ScriptedChatModel(responses=[
        tool_call("list_template_fields", {"template": "weekly"}), "칸을 확인했습니다."]))
    job = chat.post("/chat/api/run", json={"key": "s1_01", "template": "weekly"}).json()
    v = wait(chat, job["id"])
    types = [e["type"] for e in v["events"]]
    assert v["status"] == "done"
    assert any(e["type"] == "msdf" and e["order"] == "memo_text" for e in v["events"])      # 예시 메모는 MSDF로
    ai = next(e for e in v["events"] if e["type"] == "ai" and e["tool_calls"])
    assert ai["tool_calls"][0]["name"] == "list_template_fields"
    assert "tool" in types and v["result"]["final_answer"] == "칸을 확인했습니다."


def test_hitl_waits_then_submits(chat, gw_env):
    def section(msgs):
        ev = msgs[-1].content.split("[기록]", 1)[1]
        refs = monthly_report.refs(ev)
        return f"내용 [{refs[0]}]." if refs else "기록 없음."
    runner.set_llm(ScriptedChatModel(responses=[section] * 4))
    job = chat.post("/chat/api/run", json={"key": "s4_04", "month": "2026-10"}).json()
    v = wait(chat, job["id"])
    assert v["status"] == "waiting" and v["interrupt"]["draft"]["line"] == ["jiyoung", "dongho"]
    assert any(f.endswith(".docx") for f in v["result"]["files"])
    chat.post(f"/chat/api/run/{job['id']}/resume", json={"action": "approve"})
    v = wait(chat, job["id"], until=("done", "error"))
    assert v["status"] == "done" and v["result"]["submitted"]["status"] == "pending"
    f = v["result"]["files"][0]
    assert chat.get(f"/chat/files/{f}").status_code == 200
    assert chat.get("/chat/files/..%2Fpytest.ini").status_code == 404


def test_sse_streams_steps_then_status(chat):
    runner.set_llm(ScriptedChatModel(responses=[
        tool_call("list_template_fields", {"template": "weekly"}), "칸을 확인했습니다."]))
    job = chat.post("/chat/api/run", json={"key": "s1_01", "template": "weekly"}).json()
    wait(chat, job["id"])
    with chat.stream("GET", f"/chat/api/run/{job['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    blocks = [b for b in text.split("\n\n") if b.startswith(("id:", "event:"))]
    kinds = [next(l for l in b.splitlines() if l.startswith("event:"))[7:] for b in blocks]
    assert kinds.count("status") == 1 and kinds[-1] == "status" and kinds.count("step") >= 3
    last = json.loads(blocks[-1].split("data: ", 1)[1])
    assert last["status"] == "done" and last["result"]["final_answer"] == "칸을 확인했습니다."
    # 다시 붙을 때는 Last-Event-ID 다음부터
    with chat.stream("GET", f"/chat/api/run/{job['id']}/events", headers={"Last-Event-ID": "1"}) as r:
        again = "".join(r.iter_text())
    assert "id: 0\n" not in again and "id: 2\n" in again


def test_cors_allows_groupware_origin(chat):
    r = chat.options("/chat/api/run", headers={"Origin": "http://127.0.0.1:8100", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8100"
