"""그룹웨어 서버: 메일·일정·결재 API + 변경 이벤트(outbox/NOTIFY, webhook) + 웹 화면(React 빌드).

    docker compose -f infra/docker-compose.yml up -d
    python -m infra.groupware.seed
    uvicorn infra.groupware.app:app --port 8100

웹 화면은 web/ (React). 개발 중에는 `npm run dev`(5173)가 /api를 이 서버로 넘긴다.
"""

from __future__ import annotations

import base64
from urllib.parse import quote
import threading
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from infra.groupware import db
from infra.groupware.seed import add_attendees, add_line, add_recipients

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _range(col: str, since: str | None, until: str | None) -> tuple[str, list]:
    sql, args = "", []
    if since:
        sql += f" AND {col} >= %s"
        args.append(since)
    if until:
        sql += f" AND {col} < %s"
        args.append(until)
    return sql, args


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class MailIn(BaseModel):
    to: list[str]
    cc: list[str] = []
    subject: str
    body: str = ""


class EventIn(BaseModel):
    title: str
    start: str
    end: str
    location: str = ""
    attendees: list[str] = []
    memo: str = ""


class ApprovalIn(BaseModel):
    kind: str
    title: str
    body: str = ""
    line: list[str]
    attachment_b64: str | None = None
    attachment_name: str | None = None


class DecideIn(BaseModel):
    decision: str          # approve | reject
    comment: str = ""


class HookIn(BaseModel):
    url: str


MAIL_SELECT = """
SELECT m.id, m.sender, m.subject, m.body, m.sent_at,
  coalesce((SELECT array_agg(user_id ORDER BY seq) FROM mail_recipients r WHERE r.mail_id=m.id AND kind='to'), '{}') AS recipients,
  coalesce((SELECT array_agg(user_id ORDER BY seq) FROM mail_recipients r WHERE r.mail_id=m.id AND kind='cc'), '{}') AS cc,
  (SELECT bool_and(read_at IS NOT NULL) FROM mail_recipients r WHERE r.mail_id=m.id AND r.user_id=%(me)s) AS read
FROM mails m WHERE 1=1"""

EVENT_SELECT = """
SELECT e.id, e.organizer, e.title, e.start_at AS start, e.end_at AS "end", e.location, e.memo,
  coalesce((SELECT array_agg(user_id ORDER BY seq) FROM event_attendees a WHERE a.event_id=e.id), '{}') AS attendees
FROM events e WHERE 1=1"""

APPROVAL_SELECT = """
SELECT a.id, a.drafter, a.kind, a.title, a.body, a.status, a.step, a.created_at, a.decided_at,
  (SELECT array_agg(approver ORDER BY seq) FROM approval_lines l WHERE l.approval_id=a.id) AS line,
  coalesce((SELECT json_agg(json_build_object(
              'by', approver, 'decision', decision, 'comment', comment,
              'at', to_char(decided_at, 'YYYY-MM-DD"T"HH24:MI:SS')) ORDER BY seq)
            FROM approval_lines l WHERE l.approval_id=a.id AND decision IS NOT NULL), '[]') AS history,
  (SELECT name FROM approval_attachments t WHERE t.approval_id=a.id) AS attachment_name
FROM approvals a WHERE 1=1"""


def create_app(dsn: str = db.DSN, schema: str = db.SCHEMA, *, notify_async: bool = True) -> FastAPI:
    app = FastAPI(title="놀러가자 그룹웨어 (실습용)")
    store = db.Store(dsn, schema)
    channel = f"{schema}_events"
    app.state.store = store
    app.state.notified = []   # 보낸 변경 알림 기록 (테스트·확인용)

    @app.on_event("shutdown")
    def _close():
        store.close()

    # ---------------- 변경 이벤트 ----------------
    def emit(c, kind: str, ref_id: int, users: list[str], **payload) -> dict:
        """상태를 바꾼 트랜잭션 안에서 부른다: outbox 한 행 + NOTIFY(커밋될 때 전달)."""
        users = sorted(set(users))
        ev = c.execute("INSERT INTO outbox_events(kind,ref_id,users,payload) VALUES(%s,%s,%s,%s) RETURNING id, created_at",
                       (kind, ref_id, users, Jsonb(payload))).fetchone()
        c.execute("SELECT pg_notify(%s, %s)", (channel, str(ev["id"])))
        return {"event_id": ev["id"], "kind": kind, "ref_id": ref_id, "users": users, "at": db.iso(ev["created_at"]), **payload}

    def after_commit(ev: dict) -> None:
        """커밋된 뒤: 기록하고 외부 webhook으로도 보낸다(3편 MSDF 수집이 이것을 듣는다)."""
        app.state.notified.append(ev)
        urls = [r["url"] for r in store.all("SELECT url FROM webhooks")]

        def send():
            for url in urls:
                try:
                    httpx.post(url, json=ev, timeout=3)
                except httpx.HTTPError:
                    pass
        if urls:
            threading.Thread(target=send, daemon=True).start() if notify_async else send()

    def user(x_user: str | None = Header(default=None)) -> str:
        if not x_user:
            raise HTTPException(401, "X-User 헤더가 필요합니다")
        if store.one("SELECT 1 AS ok FROM users WHERE id=%s", (x_user,)) is None:
            raise HTTPException(403, f"없는 사용자입니다: {x_user}")
        return x_user

    # ---------------- 사용자 ----------------
    @app.get("/api/users")
    def users():
        return store.all("SELECT id, name, team, title FROM users ORDER BY id")

    @app.get("/api/summary")
    def summary(me: str = Depends(user)):
        """화면 배지용: 안 읽은 메일, 내 차례인 결재, 오늘 일정 수."""
        return store.one("""
            SELECT
              (SELECT count(*) FROM mail_recipients WHERE user_id=%(me)s AND read_at IS NULL) AS unread_mails,
              (SELECT count(*) FROM approvals a JOIN approval_lines l ON l.approval_id=a.id AND l.seq=a.step
                 WHERE a.status='pending' AND l.approver=%(me)s) AS my_turn_approvals,
              (SELECT max(id) FROM outbox_events WHERE %(me)s = ANY(users)) AS last_event_id
        """, {"me": me})

    @app.get("/api/notifications")
    def notifications(after: int = 0, limit: int = 30, me: str = Depends(user)):
        return store.all("SELECT id, kind, ref_id, payload, created_at FROM outbox_events"
                         " WHERE %s = ANY(users) AND id > %s ORDER BY id DESC LIMIT %s", (me, after, limit))

    # ---------------- 메일 ----------------
    def list_mails(me: str, box: str, since, until, q) -> list[dict]:
        sql, args = _range("m.sent_at", since, until)
        params = {"me": me}
        if box == "sent":
            sql += " AND m.sender=%(me)s"
        else:
            sql += " AND EXISTS (SELECT 1 FROM mail_recipients x WHERE x.mail_id=m.id AND x.user_id=%(me)s)"
        if q:
            sql += " AND (m.subject ILIKE %(q)s OR m.body ILIKE %(q)s)"
            params["q"] = _like(q)
        # 범위 인자는 이름 붙은 인자로 바꿔 넣는다
        for i, a in enumerate(args):
            sql = sql.replace("%s", f"%(r{i})s", 1)
            params[f"r{i}"] = a
        return store.all(MAIL_SELECT + sql + " ORDER BY m.sent_at, m.id", params)

    @app.get("/api/mails")
    def get_mails(box: str = "inbox", since: str | None = None, until: str | None = None,
                  q: str | None = None, me: str = Depends(user)):
        return list_mails(me, box, since, until, q)

    @app.get("/api/mails/{mail_id}")
    def get_mail(mail_id: int, me: str = Depends(user)):
        m = store.one(MAIL_SELECT + " AND m.id=%(id)s", {"me": me, "id": mail_id})
        if m is None or me not in [m["sender"], *m["recipients"], *m["cc"]]:
            raise HTTPException(404, "메일이 없습니다")
        return m

    @app.post("/api/mails/{mail_id}/read")
    def read_mail(mail_id: int, me: str = Depends(user)):
        with store.tx() as c:
            n = c.execute("UPDATE mail_recipients SET read_at=%s WHERE mail_id=%s AND user_id=%s AND read_at IS NULL",
                          (_now(), mail_id, me)).rowcount
        return {"ok": True, "changed": n}

    @app.post("/api/mails")
    def send_mail(mail: MailIn, me: str = Depends(user)):
        if not mail.to:
            raise HTTPException(400, "받는 사람이 없습니다")
        with store.tx() as c:
            mid = c.execute("INSERT INTO mails(sender,subject,body,sent_at) VALUES(%s,%s,%s,%s) RETURNING id",
                            (me, mail.subject, mail.body, _now())).fetchone()["id"]
            add_recipients(c, mid, mail.to, mail.cc)
            ev = emit(c, "mails", mid, [me, *mail.to, *mail.cc], subject=mail.subject, by=me)
        after_commit(ev)
        return {"id": mid}

    # ---------------- 일정 ----------------
    @app.get("/api/events")
    def get_events(since: str | None = None, until: str | None = None, me: str = Depends(user)):
        sql, args = _range("e.start_at", since, until)
        sql += (" AND (e.organizer=%s OR EXISTS (SELECT 1 FROM event_attendees x WHERE x.event_id=e.id AND x.user_id=%s))"
                " ORDER BY e.start_at, e.id")
        return store.all(EVENT_SELECT + sql, [*args, me, me])

    @app.get("/api/events/{event_id}")
    def get_event(event_id: int, me: str = Depends(user)):
        e = store.one(EVENT_SELECT + " AND e.id=%s", (event_id,))
        if e is None or me not in [e["organizer"], *e["attendees"]]:
            raise HTTPException(404, "일정이 없습니다")
        return e

    @app.post("/api/events")
    def add_event(ev: EventIn, me: str = Depends(user)):
        if ev.end < ev.start:
            raise HTTPException(400, "끝나는 시각이 시작보다 빠릅니다")
        with store.tx() as c:
            eid = c.execute("INSERT INTO events(organizer,title,start_at,end_at,location,memo)"
                            " VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                            (me, ev.title, ev.start, ev.end, ev.location, ev.memo)).fetchone()["id"]
            add_attendees(c, eid, [u for u in ev.attendees if u != me])
            out = emit(c, "events", eid, [me, *ev.attendees], title=ev.title, by=me)
        after_commit(out)
        return {"id": eid}

    # ---------------- 결재 ----------------
    def list_approvals(me: str, role: str, status, since, until) -> list[dict]:
        sql, args = _range("a.created_at", since, until)
        if status:
            sql += " AND a.status=%s"
            args.append(status)
        if role == "approver":
            sql += " AND EXISTS (SELECT 1 FROM approval_lines l WHERE l.approval_id=a.id AND l.approver=%s)"
        elif role == "todo":       # 지금 내 차례
            sql += (" AND a.status='pending' AND EXISTS (SELECT 1 FROM approval_lines l"
                    " WHERE l.approval_id=a.id AND l.seq=a.step AND l.approver=%s)")
        else:
            sql += " AND a.drafter=%s"
        return store.all(APPROVAL_SELECT + sql + " ORDER BY a.created_at, a.id", [*args, me])

    @app.get("/api/approvals")
    def get_approvals(role: str = "drafter", status: str | None = None, since: str | None = None,
                      until: str | None = None, me: str = Depends(user)):
        return list_approvals(me, role, status, since, until)

    @app.get("/api/approvals/{approval_id}")
    def get_approval(approval_id: int, me: str = Depends(user)):
        a = store.one(APPROVAL_SELECT + " AND a.id=%s", (approval_id,))
        if a is None or (me != a["drafter"] and me not in a["line"]):
            raise HTTPException(404, "결재 문서가 없습니다")
        return a

    @app.post("/api/approvals")
    def submit(ap: ApprovalIn, me: str = Depends(user)):
        if not ap.line:
            raise HTTPException(400, "결재선이 비어 있습니다")
        blob = base64.b64decode(ap.attachment_b64) if ap.attachment_b64 else None
        with store.tx() as c:
            aid = c.execute("INSERT INTO approvals(drafter,kind,title,body,created_at) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                            (me, ap.kind, ap.title, ap.body, _now())).fetchone()["id"]
            add_line(c, aid, ap.line)
            if blob is not None:
                c.execute("INSERT INTO approval_attachments(approval_id,name,content) VALUES(%s,%s,%s)",
                          (aid, ap.attachment_name or "attachment", blob))
            ev = emit(c, "approvals", aid, [me, *ap.line], status="pending", title=ap.title, by=me)
        after_commit(ev)
        return {"id": aid, "status": "pending", "next": ap.line[0]}

    def decide_core(approval_id: int, me: str, decision: str, comment: str) -> dict:
        with store.tx() as c:
            a = c.execute("SELECT * FROM approvals WHERE id=%s FOR UPDATE", (approval_id,)).fetchone()
            if a is None:
                raise HTTPException(404, "결재 문서가 없습니다")
            line = [r["approver"] for r in c.execute(
                "SELECT approver FROM approval_lines WHERE approval_id=%s ORDER BY seq", (approval_id,))]
            if a["status"] != "pending":
                raise HTTPException(409, f"이미 끝난 결재입니다 ({a['status']})")
            if line[a["step"]] != me:
                raise HTTPException(403, f"지금은 {line[a['step']]}의 차례입니다")
            if decision not in ("approve", "reject"):
                raise HTTPException(400, "decision은 approve 또는 reject")
            now = _now()
            c.execute("UPDATE approval_lines SET decision=%s, comment=%s, decided_at=%s WHERE approval_id=%s AND seq=%s",
                      (decision, comment, now, approval_id, a["step"]))
            step, status, decided = a["step"] + 1, "pending", None
            if decision == "reject":
                status, decided = "rejected", now
            elif step >= len(line):
                status, decided = "approved", now
            c.execute("UPDATE approvals SET step=%s, status=%s, decided_at=%s WHERE id=%s",
                      (step, status, decided, approval_id))
            ev = emit(c, "approvals", approval_id, [a["drafter"], *line], status=status, title=a["title"],
                      by=me, decision=decision)
        after_commit(ev)
        return {"id": approval_id, "status": status, "next": line[step] if status == "pending" else None}

    @app.post("/api/approvals/{approval_id}/decide")
    def decide(approval_id: int, body: DecideIn, me: str = Depends(user)):
        return decide_core(approval_id, me, body.decision, body.comment)

    @app.get("/api/approvals/{approval_id}/attachment")
    def attachment(approval_id: int, me: str = Depends(user)):
        a = store.one(APPROVAL_SELECT + " AND a.id=%s", (approval_id,))
        if a is None:
            raise HTTPException(404, "결재 문서가 없습니다")
        if me != a["drafter"] and me not in a["line"]:
            raise HTTPException(403, "볼 수 없는 문서입니다")
        with store.tx() as c:
            r = c.execute("SELECT name, content FROM approval_attachments WHERE approval_id=%s",
                          (approval_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "첨부가 없습니다")
        return Response(bytes(r["content"]), media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(r['name'])}"})

    # ---------------- webhook ----------------
    @app.post("/api/webhooks")
    def add_hook(h: HookIn):
        with store.tx() as c:
            c.execute("INSERT INTO webhooks(url) VALUES(%s) ON CONFLICT DO NOTHING", (h.url,))
        return {"ok": True}

    app.state.decide_core = decide_core
    return app


def mount_web(app: FastAPI) -> FastAPI:
    """React 빌드(web/dist)를 / 에 붙인다. 다른 라우터를 다 붙인 뒤 마지막에 부른다."""
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_build():
            return HTMLResponse("<meta charset=utf-8><p>웹 화면이 아직 빌드되지 않았습니다. "
                                "<code>cd web; npm run build</code> 또는 개발 서버 <code>npm run dev</code>(5173)를 쓰세요.</p>")
    return app


def __getattr__(name: str):
    """`uvicorn infra.groupware.app:app` 때만 앱(=DB 연결 풀)을 만든다. import만 해서는 DB에 붙지 않는다."""
    if name == "app":
        globals()["app"] = mount_web(create_app())
        return globals()["app"]
    raise AttributeError(name)
