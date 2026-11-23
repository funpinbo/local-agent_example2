"""3단계 그룹웨어 도구. 데이터는 MSDF에 결과물 이름(gw_mails, gw_events, gw_approvals, gw_month)으로 요청한다.

보고서 작성자는 이재휘(jaehwi)으로 고정한다. 근거 id는 mail:12, event:3, approval:5 형식이다.
"""

from __future__ import annotations

import json
import os

from langchain.tools import tool

from common import config, monthly_report
from infra.msdf.services import agent

USER = os.getenv("REPORT_USER", "jaehwi")
MONTHS = ["2026-10", "2026-11"]


def _fetch(kind: str, month: str):
    return agent.fetch(f"gw_{kind}", {"user": USER, "month": month})


def mails(month: str) -> list[dict]:
    return _fetch("mails", month)


def events(month: str) -> list[dict]:
    return _fetch("events", month)


def approvals(month: str) -> list[dict]:
    return _fetch("approvals", month)


def digest(month: str) -> dict:
    return _fetch("month", month)


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _find(kind: str, item_id: int) -> dict | None:
    for m in MONTHS:
        for x in _fetch(kind, m):
            if x["id"] == int(item_id):
                return x
    return None


# ---------------- 메일 ----------------
@tool
def gw_list_mails(month: str) -> str:
    """month(예: 2026-10)에 주고받은 메일 목록을 보여 준다(id, 시각, 보낸 사람, 받는 사람, 제목). 본문은 gw_read_mail로 본다."""
    return _j([{"ref": f"mail:{m['id']}", "at": m["sent_at"], "from": m["sender"], "to": m["recipients"],
                "subject": m["subject"]} for m in mails(month)])


@tool
def gw_read_mail(mail_id: int) -> str:
    """메일 하나의 본문을 읽는다. mail_id는 숫자(mail:12의 12)."""
    m = _find("mails", mail_id)
    return _j({"ref": f"mail:{m['id']}", **{k: m[k] for k in ("sent_at", "sender", "recipients", "subject", "body")}}) if m else "그런 메일이 없습니다."


@tool
def gw_search_mails(keyword: str, month: str) -> str:
    """month의 메일 중 제목이나 본문에 keyword가 들어간 것을 찾는다."""
    hits = [m for m in mails(month) if keyword in m["subject"] or keyword in m["body"]]
    return _j([{"ref": f"mail:{m['id']}", "at": m["sent_at"], "subject": m["subject"], "body": m["body"]} for m in hits]) or "찾은 메일이 없습니다."


# ---------------- 일정 ----------------
@tool
def gw_list_events(month: str) -> str:
    """month에 주최하거나 참석한 일정 목록(id, 시작, 제목, 장소)을 보여 준다."""
    return _j([{"ref": f"event:{e['id']}", "start": e["start"], "title": e["title"], "location": e["location"]}
               for e in events(month)])


@tool
def gw_read_event(event_id: int) -> str:
    """일정 하나의 자세한 내용(참석자, 메모)을 본다. event_id는 숫자."""
    e = _find("events", event_id)
    return _j({"ref": f"event:{e['id']}", **{k: e[k] for k in ("start", "end", "title", "location", "organizer", "attendees", "memo")}}) if e else "그런 일정이 없습니다."


# ---------------- 결재 ----------------
@tool
def gw_list_approvals(month: str) -> str:
    """month에 기안했거나 결재선에 있는 결재 문서 목록(id, 종류, 제목, 상태)을 보여 준다."""
    return _j([{"ref": f"approval:{a['id']}", "created_at": a["created_at"], "kind": a["kind"], "title": a["title"],
                "status": a["status"]} for a in approvals(month)])


@tool
def gw_read_approval(approval_id: int) -> str:
    """결재 문서 하나의 본문, 결재선, 결정 이력(의견 포함)을 본다. approval_id는 숫자."""
    a = _find("approvals", approval_id)
    return _j({"ref": f"approval:{a['id']}", **{k: a[k] for k in ("kind", "title", "body", "line", "status", "history")}}) if a else "그런 결재가 없습니다."


# ---------------- 묶음 ----------------
@tool
def gw_month_digest(month: str) -> str:
    """month의 메일·일정·결재를 시간순으로 합친 전체 기록을 한 번에 본다. 기록이 많으면 길다."""
    d = digest(month)
    return _j({"counts": d["counts"], "items": [{"ref": i["ref"], "at": i["at"], "kind": i["kind"], "text": i["text"]}
                                               for i in d["items"]]})


@tool
def gw_count_by_kind(month: str) -> str:
    """month의 메일·일정·결재 건수만 센다."""
    return _j(digest(month)["counts"])


@tool
def gw_people() -> str:
    """그룹웨어 사용자 목록(아이디, 이름, 팀, 직급). 아이디를 이름으로 바꿀 때 쓴다."""
    return _j(agent.fetch("gw_people", {}))


@tool
def save_monthly_report(title: str, sections: list[dict]) -> str:
    """월간 보고서를 docx로 저장한다. sections는 [{"heading": 섹션 제목, "body": 본문}] 목록이다. 본문 문장마다 [mail:12]처럼 근거 id를 단다."""
    path = monthly_report.save(title, sections, stem="s3_monthly")
    return f"저장했습니다: {path.name}"


DATA_TOOLS = [gw_list_mails, gw_read_mail, gw_search_mails, gw_list_events, gw_read_event,
              gw_list_approvals, gw_read_approval, gw_month_digest, gw_count_by_kind, gw_people]
MAIL_TOOLS = [gw_list_mails, gw_read_mail, gw_search_mails]
EVENT_TOOLS = [gw_list_events, gw_read_event]
APPROVAL_TOOLS = [gw_list_approvals, gw_read_approval]
