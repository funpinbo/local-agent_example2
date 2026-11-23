import socket
import threading
import time

import pytest
import uvicorn

from infra.groupware.app import create_app
from infra.groupware.seed import seed
from infra.msdf.services import agent, groupware_sync


@pytest.fixture()
def msdf(tmp_path):
    agent.close()
    agent.connect("local", db=tmp_path / "msdf.db")
    yield agent
    agent.close()


@pytest.fixture()
def groupware(tmp_path, monkeypatch, pg_schema):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(schema=pg_schema), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(groupware_sync, "GROUPWARE_URL", f"http://127.0.0.1:{port}")
    yield
    server.should_exit = True
    t.join(5)


def test_search_hits_have_source(msdf):
    hits = msdf.fetch("doc_search", {"corpus": "llm_server", "q": "OOM 원인 메모리 부족", "k": 3})
    assert hits and len(hits) <= 3
    assert hits[0]["source"] == "03_oom.md"
    assert all({"chunk_id", "source", "text", "score"} <= h.keys() for h in hits)


def test_second_question_reuses_index(msdf):
    msdf.fetch("doc_search", {"corpus": "llm_server", "q": "로그 보관 기간", "k": 2})
    first = msdf.runner().last.plan
    assert {s.node for s in first.steps} >= {"load", "chunk", "index", "query", "search"}
    hits = msdf.fetch("doc_search", {"corpus": "llm_server", "q": "검색 응답 지연", "k": 2})
    second = msdf.runner().last.plan
    assert set(second.cached) >= {"load", "chunk", "index"}
    assert {s.node for s in second.steps} == {"query", "search"}
    assert hits[0]["source"] == "05_search_api.md"


def test_catalog(msdf):
    cat = msdf.fetch("doc_catalog", {"corpus": "llm_server"})
    assert len(cat) == 6
    oom = next(c for c in cat if c["source"] == "03_oom.md")
    assert oom["title"] == "메모리 부족(OOM) 대응" and "주요 원인" in oom["headings"]


def test_gw_month_digest(msdf, groupware):
    d = msdf.fetch("gw_month", {"user": "jaehwi", "month": "2026-10"})
    c = d["counts"]
    assert c["mails"] > 15 and c["events"] >= 8 and c["approvals"] == 6
    refs = [i["ref"] for i in d["items"]]
    assert any(r.startswith("mail:") for r in refs) and any(r.startswith("approval:") for r in refs)
    assert [i["at"] for i in d["items"]] == sorted(i["at"] for i in d["items"])
    nov = msdf.fetch("gw_approvals", {"user": "jaehwi", "month": "2026-11"})
    assert nov == []


def test_failure_raises(msdf):
    with pytest.raises(agent.FetchError):
        msdf.fetch("doc_search", {"corpus": "없는묶음", "q": "x", "k": 1})
