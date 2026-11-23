import base64

import pytest
from fastapi.testclient import TestClient

from infra.groupware.app import create_app

OCT = {"since": "2026-10-01", "until": "2026-11-01"}
NOV = {"since": "2026-11-01", "until": "2026-12-01"}


@pytest.fixture()
def client(pg_schema):
    app = create_app(schema=pg_schema, notify_async=False)
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def H(user):
    return {"X-User": user}


def test_user_header_required(client):
    assert client.get("/api/mails").status_code == 401
    assert client.get("/api/mails", headers=H("nobody")).status_code == 403


def test_month_filter_and_attendee_events(client):
    oct_mails = client.get("/api/mails", params=OCT, headers=H("jaehwi")).json()
    nov_mails = client.get("/api/mails", params=NOV, headers=H("jaehwi")).json()
    assert oct_mails and nov_mails
    assert all(m["sent_at"].startswith("2026-10") for m in oct_mails)
    # 참석자로만 들어간 일정도 보인다
    evs = client.get("/api/events", params=OCT, headers=H("jaehwi")).json()
    assert any(e["organizer"] != "jaehwi" and "jaehwi" in e["attendees"] for e in evs)
    sent = client.get("/api/mails", params={"box": "sent", **OCT}, headers=H("jaehwi")).json()
    assert all(m["sender"] == "jaehwi" for m in sent)


def test_october_approvals_and_empty_november(client):
    octs = client.get("/api/approvals", params=OCT, headers=H("jaehwi")).json()
    statuses = sorted(a["status"] for a in octs)
    assert "rejected" in statuses and statuses.count("approved") >= 4
    rejected = next(a for a in octs if a["status"] == "rejected")
    assert "90일" in rejected["title"] and rejected["history"][-1]["by"] == "taeyang"
    assert client.get("/api/approvals", params=NOV, headers=H("jaehwi")).json() == []


def test_submit_turn_order_approve_attachment(client):
    blob = base64.b64encode("보고서".encode()).decode()
    r = client.post("/api/approvals", headers=H("jaehwi"), json={
        "kind": "월간보고", "title": "11월 운영 보고", "line": ["jiyoung", "dongho"],
        "attachment_b64": blob, "attachment_name": "report.docx"}).json()
    aid = r["id"]
    # 차례가 아닌 사람은 결정할 수 없다
    assert client.post(f"/api/approvals/{aid}/decide", headers=H("dongho"),
                       json={"decision": "approve"}).status_code == 403
    assert client.post(f"/api/approvals/{aid}/decide", headers=H("jiyoung"),
                       json={"decision": "approve"}).json()["next"] == "dongho"
    assert client.post(f"/api/approvals/{aid}/decide", headers=H("dongho"),
                       json={"decision": "approve"}).json()["status"] == "approved"
    att = client.get(f"/api/approvals/{aid}/attachment", headers=H("dongho"))
    assert att.status_code == 200 and att.content == "보고서".encode()
    assert client.get(f"/api/approvals/{aid}/attachment", headers=H("haeun")).status_code == 403


def test_reject_ends_and_webhook_notified(client):
    client.post("/api/webhooks", json={"url": "http://127.0.0.1:9/never"})
    aid = client.post("/api/approvals", headers=H("jaehwi"), json={
        "kind": "구매품의", "title": "GPU 서버", "line": ["jiyoung", "dongho"]}).json()["id"]
    r = client.post(f"/api/approvals/{aid}/decide", headers=H("jiyoung"), json={"decision": "reject"}).json()
    assert r["status"] == "rejected"
    assert client.post(f"/api/approvals/{aid}/decide", headers=H("dongho"),
                       json={"decision": "approve"}).status_code == 409
    client.post("/api/mails", headers=H("haeun"), json={"to": ["jaehwi"], "subject": "안녕", "body": "테스트"})
    kinds = [n["kind"] for n in client.app_ref.state.notified]
    assert kinds == ["approvals", "approvals", "mails"]
    assert "jaehwi" in client.app_ref.state.notified[-1]["users"]


def test_todo_read_and_notifications(client):
    aid = client.post("/api/approvals", headers=H("jaehwi"), json={
        "kind": "출장신청", "title": "테스트 출장", "line": ["jiyoung"]}).json()["id"]
    todo = client.get("/api/approvals", params={"role": "todo"}, headers=H("jiyoung")).json()
    assert [a["id"] for a in todo] == [aid]
    assert client.get("/api/summary", headers=H("jiyoung")).json()["my_turn_approvals"] == 1
    # 새 메일은 안 읽음 → 읽음 처리
    mid = client.post("/api/mails", headers=H("haeun"), json={"to": ["jaehwi"], "subject": "확인", "body": ""}).json()["id"]
    assert client.get(f"/api/mails/{mid}", headers=H("jaehwi")).json()["read"] is False
    assert client.get("/api/summary", headers=H("jaehwi")).json()["unread_mails"] == 1
    client.post(f"/api/mails/{mid}/read", headers=H("jaehwi"))
    assert client.get(f"/api/mails/{mid}", headers=H("jaehwi")).json()["read"] is True
    # 변경 이벤트(outbox)는 관련된 사람에게만 보인다
    kinds = [n["kind"] for n in client.get("/api/notifications", headers=H("jaehwi")).json()]
    assert kinds == ["mails", "approvals"]
    assert client.get("/api/notifications", headers=H("dongho")).json() == []
