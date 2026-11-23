"""그룹웨어 MCP 서버 (4편 3장). 비서 코드 안에 있던 그룹웨어 도구를 별도 서버로 꺼냈다.

    .\\.venv\\Scripts\\python.exe -m chat_ui.mcp_servers.groupware        # http://127.0.0.1:8301/mcp

안에서는 그룹웨어 REST(8100)를 부른다. 비서도, 다른 Agent(6장 일정 조율 Agent)도 이 서버 하나를 같이 쓴다.

1장·2장에서 본 실패를 서버 설계에 반영했다.
- 기간: 한 달(month)만 받던 3편 도구 → since/until 날짜 범위. 둘 다 비우면 전체.
- 찾기와 읽기: 목록에 근거 id·사람 이름·앞부분을 같이 준다. 결재 목록에는 마지막 결재 의견(반려 사유)까지.
- 사람: 아이디 대신 이름으로 보여 주고, 쓰기 도구는 이름도 받는다(서버가 아이디로 바꾼다).
- 설명: 짧게. 도구 10개(읽기 6 + 쓰기 4).
- 쓰기 도구에는 readOnlyHint=false를 단다. 실행 전 사람 승인은 이 서버가 아니라 비서(클라이언트)가 한다.

인증(실습용으로 단순화):
- 서비스 키: Authorization: Bearer $GROUPWARE_MCP_KEY — 누가 이 서버를 부를 수 있나.
- 사용자: 요청 _meta의 "local.office/user" — 누구 대신 부르나. 그룹웨어에는 그 사용자의 X-User로 간다.
  실제 서비스라면 사용자별 OAuth 토큰(위임)이 들어갈 자리다.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError   # 이 오류의 글은 그대로 모델에게 간다(다른 예외는 "Error executing tool"로 가려진다)
from mcp_types import ToolAnnotations

from common import config

USER_META_KEY = "local.office/user"
READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

server = MCPServer(
    "office-groupware", version="0.1.0",
    instructions="놀러가자 그룹웨어(메일·일정·결재). 결과의 ref(mail:12 등)를 근거 표시에 쓴다.",
)


# ---------------------------------------------------------------- 그룹웨어 REST

def _base() -> str:
    return os.getenv("GROUPWARE_URL", config.GROUPWARE_URL).rstrip("/")


def _user(ctx: Context) -> str:
    meta = ctx.request_context.meta or {}
    user = meta.get(USER_META_KEY) if isinstance(meta, dict) else None
    if not user:
        raise ToolError(f"누구 대신 부르는지 알 수 없습니다. 요청 _meta에 '{USER_META_KEY}'가 필요합니다.")
    return str(user)


def _get(ctx: Context, path: str, **params):
    r = httpx.get(f"{_base()}/api{path}", headers={"X-User": _user(ctx)}, timeout=15,
                  params={k: v for k, v in params.items() if v not in (None, "")})
    if r.status_code >= 400:
        raise ToolError(f"그룹웨어 오류 {r.status_code}: {r.json().get('detail', r.text)}")
    return r.json()


def _post(ctx: Context, path: str, body: dict):
    r = httpx.post(f"{_base()}/api{path}", headers={"X-User": _user(ctx)}, json=body, timeout=15)
    if r.status_code >= 400:
        raise ToolError(f"그룹웨어 오류 {r.status_code}: {r.json().get('detail', r.text)}")
    return r.json()


_people_cache: list[dict] | None = None


def _people() -> list[dict]:
    global _people_cache
    if _people_cache is None:
        _people_cache = httpx.get(f"{_base()}/api/users", timeout=15).json()
    return _people_cache


def _name(uid: str) -> str:
    return next((f"{p['name']}({p['team']})" for p in _people() if p["id"] == uid), uid)


def _to_id(who: str) -> str:
    """아이디, 이름, '이름 직급' 모두 받는다."""
    w = who.strip()
    for p in _people():
        if w in (p["id"], p["name"]) or w.startswith(p["name"]):
            return p["id"]
    raise ToolError(f"'{who}'에 해당하는 사람이 없습니다. people 도구로 찾아보세요.")


def _until_inclusive(until: str) -> str:
    """사람은 until을 그날까지로 쓴다 → API는 그 다음 날 0시 미만."""
    if not until:
        return ""
    return (date.fromisoformat(until[:10]) + timedelta(days=1)).isoformat()


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


# ---------------------------------------------------------------- 읽기 도구

@server.tool(annotations=READ)
def find_mails(ctx: Context, query: str = "", since: str = "", until: str = "",
               box: Literal["inbox", "sent", "all"] = "all", limit: int = 20) -> str:
    """메일을 찾는다. query=제목·본문 검색어, since/until=YYYY-MM-DD(비우면 전체). 최근 것부터. 본문 전체는 read_mail."""
    boxes = ["inbox", "sent"] if box == "all" else [box]
    rows: dict[int, dict] = {}
    for b in boxes:
        for m in _get(ctx, "/mails", box=b, q=query, since=since, until=_until_inclusive(until)):
            rows[m["id"]] = m
    out = sorted(rows.values(), key=lambda m: m["sent_at"], reverse=True)[:limit]
    return _j({"count": len(rows), "mails": [
        {"ref": f"mail:{m['id']}", "at": m["sent_at"][:16], "from": _name(m["sender"]),
         "to": [_name(u) for u in m["recipients"]], "subject": m["subject"], "snippet": m["body"][:80]} for m in out]})


@server.tool(annotations=READ)
def read_mail(ctx: Context, mail_id: int) -> str:
    """메일 하나의 전체 본문. mail_id는 숫자(mail:12면 12)."""
    m = _get(ctx, f"/mails/{int(mail_id)}")
    return _j({"ref": f"mail:{m['id']}", "at": m["sent_at"][:16], "from": _name(m["sender"]),
               "to": [_name(u) for u in m["recipients"]], "cc": [_name(u) for u in m["cc"]],
               "subject": m["subject"], "body": m["body"]})


@server.tool(annotations=READ)
def find_events(ctx: Context, since: str = "", until: str = "", query: str = "") -> str:
    """내 일정(주최·참석)을 찾는다. since/until=YYYY-MM-DD, query=제목·장소·메모 검색어."""
    evs = _get(ctx, "/events", since=since, until=_until_inclusive(until))
    if query:
        evs = [e for e in evs if query in e["title"] or query in e["location"] or query in e["memo"]]
    return _j({"count": len(evs), "events": [
        {"ref": f"event:{e['id']}", "start": e["start"][:16], "end": e["end"][:16], "title": e["title"],
         "location": e["location"], "organizer": _name(e["organizer"]), "attendees": [_name(u) for u in e["attendees"]],
         "memo": e["memo"]} for e in evs]})


def _approval_row(a: dict) -> dict:
    last = a["history"][-1] if a["history"] else None
    return {"ref": f"approval:{a['id']}", "created": a["created_at"][:16], "kind": a["kind"], "title": a["title"],
            "drafter": _name(a["drafter"]), "line": [_name(u) for u in a["line"]], "status": a["status"],
            "waiting_for": _name(a["line"][a["step"]]) if a["status"] == "pending" else None,
            "last_decision": ({"by": _name(last["by"]), "decision": last["decision"], "comment": last["comment"]}
                              if last else None)}


@server.tool(annotations=READ)
def find_approvals(ctx: Context, query: str = "", status: Literal["", "pending", "approved", "rejected"] = "",
                   since: str = "", until: str = "", role: Literal["all", "drafter", "approver", "todo"] = "all") -> str:
    """결재 문서를 찾는다. query는 제목·본문·종류·사람 이름·팀·결재 의견에서 찾는다. 마지막 결재 의견(반려 사유)까지 준다.
    role: drafter=내가 올린, approver=내가 결재선, todo=지금 내 차례."""
    roles = ["drafter", "approver"] if role == "all" else [role]
    rows: dict[int, dict] = {}
    for r in roles:
        for a in _get(ctx, "/approvals", role=r, status=status, since=since, until=_until_inclusive(until)):
            rows[a["id"]] = a

    # 실제 실행에서 "보안팀이 반려한 결재"를 query="보안팀"으로 찾다가 0건이 나왔다(제목·본문만 보던 때).
    # 사람들이 묻는 말에는 사람·팀 이름이 들어간다 → 결재에 얽힌 사람의 이름·팀과 결재 의견도 찾는 범위에 넣었다.
    def haystack(a: dict) -> str:
        who = [a["drafter"], *a["line"]]
        return " ".join([a["title"], a["body"], a["kind"], *[_name(u) for u in who],
                         *[h["comment"] for h in a["history"]]])
    out = [a for a in rows.values() if not query or query in haystack(a)]
    out.sort(key=lambda a: a["created_at"], reverse=True)
    return _j({"count": len(out), "approvals": [_approval_row(a) for a in out]})


@server.tool(annotations=READ)
def read_approval(ctx: Context, approval_id: int) -> str:
    """결재 문서 하나: 본문, 결재선, 결재 이력(누가 언제 어떤 의견으로)."""
    a = _get(ctx, f"/approvals/{int(approval_id)}")
    return _j({**_approval_row(a), "body": a["body"], "attachment": a["attachment_name"],
               "history": [{"by": _name(h["by"]), "decision": h["decision"], "comment": h["comment"], "at": h["at"][:16]}
                           for h in a["history"]]})


@server.tool(annotations=READ)
def people(query: str = "") -> str:
    """회사 사람 목록(아이디, 이름, 팀, 직급). query=이름·팀 검색어."""
    ps = [p for p in _people() if not query or query in p["name"] or query in p["team"] or query in p["id"]]
    return _j(ps)


# ---------------------------------------------------------------- 쓰기 도구 (비서가 사람 승인을 받은 뒤 부른다)

@server.tool(annotations=WRITE)
def send_mail(ctx: Context, to: list[str], subject: str, body: str, cc: list[str] | None = None) -> str:
    """메일을 보낸다. to/cc는 이름이나 아이디 목록."""
    r = _post(ctx, "/mails", {"to": [_to_id(x) for x in to], "cc": [_to_id(x) for x in cc or []],
                              "subject": subject, "body": body})
    return _j({"sent": f"mail:{r['id']}"})


@server.tool(annotations=WRITE)
def create_event(ctx: Context, title: str, start: str, end: str, attendees: list[str] | None = None,
                 location: str = "", memo: str = "") -> str:
    """일정을 만든다. start/end=YYYY-MM-DDTHH:MM, attendees는 이름이나 아이디 목록."""
    r = _post(ctx, "/events", {"title": title, "start": start, "end": end, "location": location, "memo": memo,
                               "attendees": [_to_id(x) for x in attendees or []]})
    return _j({"created": f"event:{r['id']}"})


@server.tool(annotations=WRITE)
def submit_approval(ctx: Context, kind: str, title: str, body: str, line: list[str]) -> str:
    """결재를 올린다. line=결재자 이름·아이디를 결재 순서대로."""
    r = _post(ctx, "/approvals", {"kind": kind, "title": title, "body": body, "line": [_to_id(x) for x in line]})
    return _j({"submitted": f"approval:{r['id']}", "next": _name(r["next"])})


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                                         open_world_hint=False))
def decide_approval(ctx: Context, approval_id: int, decision: Literal["approve", "reject"], comment: str = "") -> str:
    """내 차례인 결재를 승인하거나 반려한다. 반려할 때는 comment에 사유를 쓴다."""
    r = _post(ctx, f"/approvals/{int(approval_id)}/decide", {"decision": decision, "comment": comment})
    return _j({"approval": f"approval:{approval_id}", "status": r["status"],
               "next": _name(r["next"]) if r.get("next") else None})


# ---------------------------------------------------------------- 리소스·프롬프트

@server.resource("gw://people", name="people", description="회사 사람 목록", mime_type="application/json")
def people_resource() -> str:
    return _j(_people())


@server.prompt(name="monthly_report", description="월간 업무 보고서 초안을 쓰는 순서")
def monthly_report(month: str) -> str:
    return (f"{month} 월간 업무 보고서를 쓴다.\n"
            f"1) find_mails, find_events, find_approvals로 {month}-01 ~ {month} 말일 기록을 모은다.\n"
            "2) 섹션: 주요 업무 / 장애·이슈 / 결재 / 다음 달 계획.\n"
            "3) 문장마다 근거 ref를 [mail:12]처럼 단다. 기록에 없는 내용은 쓰지 않는다.")


# ---------------------------------------------------------------- HTTP 앱 (서비스 키 확인)

def app(key: str | None = None):
    """Streamable HTTP 앱. key가 있으면 Authorization: Bearer <key>가 없는 요청을 401로 막는다."""
    from starlette.responses import JSONResponse

    inner = server.streamable_http_app(stateless_http=True)
    key = key if key is not None else os.getenv("GROUPWARE_MCP_KEY", "")

    async def asgi(scope, receive, send):
        if scope["type"] == "http" and key:
            auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            if auth != f"Bearer {key}":
                await JSONResponse({"error": "서비스 키가 없거나 틀립니다"}, status_code=401)(scope, receive, send)
                return
        await inner(scope, receive, send)
    asgi.inner = inner
    return asgi
