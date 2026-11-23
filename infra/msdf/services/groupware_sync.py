"""gw 서비스: 그룹웨어에서 한 달치 메일·일정·결재를 가져와 시간순으로 합친다.

원문 id(mail:12, event:3, approval:5)를 남겨 보고서 문장의 근거를 따라갈 수 있게 한다.
- local 모드: webhook을 받을 프로세스가 없으므로 첫 단계의 fresh_for 1m로 1분 뒤 다시 가져온다.
- remote 모드: 그룹웨어 webhook을 받아 invalidate(종류)를 부른다.

remote 모드:  python infra/msdf/services/groupware_sync.py
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from ms_dataflow import action, invalidate, service, start

service("gw")

GROUPWARE_URL = os.getenv("GROUPWARE_URL", "http://127.0.0.1:8100")
HOOK_PORT = int(os.getenv("GW_HOOK_PORT", "8790"))


def _month_range(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in month.split("-"))
    nxt = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    return f"{month}-01", f"{nxt}-01"


def _get(path: str, user: str, **params) -> list:
    r = httpx.get(f"{GROUPWARE_URL}{path}", params=params, headers={"X-User": user}, timeout=10)
    r.raise_for_status()
    return r.json()


@action(params=["user", "month"], outputs=["mails"], fresh_for="1m", retries=1)
def mails(user: str, month: str) -> list:
    since, until = _month_range(month)
    got = {m["id"]: m for box in ("inbox", "sent") for m in _get("/api/mails", user, box=box, since=since, until=until)}
    return sorted(got.values(), key=lambda m: m["sent_at"])


@action(params=["user", "month"], outputs=["events"], fresh_for="1m", retries=1)
def events(user: str, month: str) -> list:
    since, until = _month_range(month)
    return _get("/api/events", user, since=since, until=until)


@action(params=["user", "month"], outputs=["approvals"], fresh_for="1m", retries=1)
def approvals(user: str, month: str) -> list:
    since, until = _month_range(month)
    got = {a["id"]: a for role in ("drafter", "approver")
           for a in _get("/api/approvals", user, role=role, since=since, until=until)}
    return sorted(got.values(), key=lambda a: a["created_at"])


@action(outputs=["people"], fresh_for="10m", retries=1)
def people() -> list:
    """그룹웨어 사용자 목록(아이디, 이름, 팀, 직급)."""
    r = httpx.get(f"{GROUPWARE_URL}/api/users", timeout=10)
    r.raise_for_status()
    return r.json()


@action(inputs=["mails", "events", "approvals"], outputs=["digest"])
def digest(mails: list, events: list, approvals: list) -> dict:
    items = []
    for m in mails:
        items.append({"at": m["sent_at"], "ref": f"mail:{m['id']}", "kind": "메일",
                      "text": f"{m['sender']} → {', '.join(m['recipients'])}: {m['subject']} — {m['body']}"})
    for e in events:
        who = ", ".join([e["organizer"], *e["attendees"]])
        items.append({"at": e["start"], "ref": f"event:{e['id']}", "kind": "일정",
                      "text": f"{e['title']} ({e['location']}, {who})"})
    for a in approvals:
        last = a["history"][-1] if a["history"] else {}
        note = f" — {last.get('by')}: {last.get('comment')}" if last.get("comment") else ""
        items.append({"at": a["created_at"], "ref": f"approval:{a['id']}", "kind": "결재",
                      "text": f"[{a['kind']}] {a['title']} → {a['status']}{note}"})
    items.sort(key=lambda x: x["at"])
    return {"counts": {"mails": len(mails), "events": len(events), "approvals": len(approvals)},
            "items": items}


class _Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        kind = body.get("kind")
        if kind in ("mails", "events", "approvals"):
            invalidate(kind)
            print(f"[gw] webhook {kind} {body.get('users')} → invalidate({kind})", flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def serve_webhook() -> None:
    server = HTTPServer(("127.0.0.1", HOOK_PORT), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        httpx.post(f"{GROUPWARE_URL}/api/webhooks", json={"url": f"http://127.0.0.1:{HOOK_PORT}/"}, timeout=5)
        print(f"[gw] webhook 등록: http://127.0.0.1:{HOOK_PORT}/", flush=True)
    except httpx.HTTPError as e:
        print(f"[gw] webhook 등록 실패 (그룹웨어가 떠 있나요?): {e}", flush=True)


if __name__ == "__main__":
    start()
    serve_webhook()
    while True:
        time.sleep(3600)
