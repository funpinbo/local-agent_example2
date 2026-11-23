"""샘플 데이터. 가상 회사 '놀러가자', 모든 이름·수치는 지어낸 것.

- 2026년 10월: 로컬 LLM 서버 도입 한 달. 메일·일정·결재(5건)
- 2026년 11월: 메일·일정은 있고 결재는 일부러 비움 (4단계 Replanning용)

    python -m infra.groupware.seed            # 그룹웨어 스키마를 지우고 다시 만든다 (PostgreSQL)
"""

from __future__ import annotations

from infra.groupware import db

USERS = [
    ("jaehwi", "이재휘", "AI플랫폼팀", "선임"),
    ("jiyoung", "박지영", "AI플랫폼팀", "팀장"),
    ("seojun", "이서준", "AI플랫폼팀", "책임"),
    ("haeun", "최하은", "AI플랫폼팀", "사원"),
    ("dongho", "한동호", "기술본부", "본부장"),
    ("sora", "윤소라", "구매팀", "책임"),
    ("taeyang", "오태양", "보안팀", "책임"),
]

# (보낸 사람, 받는 사람, 참조, 시각, 제목, 본문)
MAILS = [
    # ---- 10월 ----
    ("jiyoung", ["jaehwi", "seojun", "haeun"], [], "2026-10-01T09:10", "로컬 LLM 서버 도입 착수",
     "이번 달부터 로컬 LLM 서버를 시범 운영합니다. 서버는 이재휘 선임, 모델 평가는 이서준 책임이 맡아 주세요. 월말에 한 달 운영 보고 부탁합니다."),
    ("jaehwi", ["jiyoung"], ["seojun"], "2026-10-02T17:40", "GPU 서버 1·2호기 세팅 완료",
     "1호기(대화용), 2호기(문서 검색·요약용) OS와 드라이버 설치를 마쳤습니다. 내일 vLLM 올립니다."),
    ("seojun", ["jaehwi"], [], "2026-10-05T11:02", "후보 모델 평가 기준 공유",
     "한국어 업무 문서 요약, 표 읽기, 도구 호출 세 가지로 평가하겠습니다. 평가셋 50문항 초안 첨부했습니다."),
    ("jaehwi", ["seojun"], [], "2026-10-05T14:20", "RE: 후보 모델 평가 기준 공유",
     "확인했습니다. 서버 쪽은 동시 요청 4개까지는 문제없이 돌아갑니다."),
    ("haeun", ["jaehwi"], [], "2026-10-07T10:15", "사내 문서 검색 데모 요청",
     "다음 주 팀 회의에서 문서 검색 데모를 보여 주실 수 있을까요? 신규 입사자 교육 자료로도 쓰고 싶습니다."),
    ("jaehwi", ["jiyoung"], [], "2026-10-08T19:05", "[장애] 2호기 OOM으로 재시작",
     "오후 4시 요청이 몰리면서 2호기가 CUDA out of memory로 내려갔습니다. gpu-memory-utilization 0.95를 0.85로 낮추고 재시작했습니다. 원인 정리해서 문서로 남기겠습니다."),
    ("jiyoung", ["jaehwi"], [], "2026-10-08T20:11", "RE: [장애] 2호기 OOM으로 재시작",
     "빠른 대응 고맙습니다. 재발 방지책도 문서에 같이 적어 주세요."),
    ("jaehwi", ["jiyoung", "seojun", "haeun"], [], "2026-10-09T15:30", "OOM 대응 문서 공유",
     "메모리 부족 원인 세 가지(utilization, max-model-len, 동시 요청 수)와 대응을 위키에 올렸습니다."),
    ("dongho", ["jiyoung"], ["jaehwi"], "2026-10-10T09:00", "로컬 LLM 사용 현황 공유 요청",
     "본부 회의에서 사용 현황을 보고하려 합니다. 사용자 수와 주요 용도를 정리해 주세요."),
    ("jaehwi", ["dongho", "jiyoung"], [], "2026-10-10T16:45", "RE: 로컬 LLM 사용 현황 공유 요청",
     "첫 주 사용자 47명, 주요 용도는 문서 요약(52%), 코드 질문(31%), 메일 초안(17%)입니다."),
    ("seojun", ["jaehwi", "jiyoung"], [], "2026-10-13T13:00", "모델 평가 1차 결과",
     "평가셋 50문항 기준 문서 요약 41점, 도구 호출 33점입니다. 도구 호출은 형식이 깨지는 경우가 있어 서버 설정을 더 봐야 합니다."),
    ("jaehwi", ["jiyoung"], [], "2026-10-14T09:20", "AI 인프라 세미나 참석 (10/15)",
     "내일 서울에서 열리는 AI 인프라 세미나에 참석합니다. 출장 신청은 결재 올렸습니다."),
    ("jaehwi", ["jiyoung"], [], "2026-10-16T10:05", "세미나 참석 결과 요약",
     "서빙 엔진 배칭 설정과 KV 캐시 관리 사례를 들었습니다. 출장 보고는 결재로 올렸습니다."),
    ("taeyang", ["jaehwi"], ["jiyoung"], "2026-10-17T14:00", "LLM 요청 로그 보관 기간 관련",
     "요청 로그에 개인정보가 섞일 수 있어 보관 기간 검토가 필요합니다. 예외 신청은 결재로 올려 주세요."),
    ("jaehwi", ["taeyang"], [], "2026-10-20T11:30", "RE: LLM 요청 로그 보관 기간 관련",
     "장애 분석에 3개월치가 필요해서 90일 보관 예외를 결재로 올렸습니다. 검토 부탁드립니다."),
    ("taeyang", ["jaehwi"], ["jiyoung"], "2026-10-22T16:10", "로그 90일 보관 예외 반려 사유",
     "프롬프트 원문이 남지 않더라도 사용자 식별 정보와 함께 90일은 깁니다. 60일 이하로 다시 신청해 주세요."),
    ("jaehwi", ["jiyoung"], [], "2026-10-23T09:40", "로그 보관 60일로 재신청 예정",
     "보안팀 의견대로 60일로 줄여 다시 올리겠습니다. 장애 분석은 60일이면 대부분 충분합니다."),
    ("sora", ["jaehwi"], [], "2026-10-21T10:00", "모니터링 라이선스 구매 진행 안내",
     "품의 승인되어 발주했습니다. 라이선스 키는 다음 주 초에 메일로 드립니다."),
    ("haeun", ["jaehwi"], [], "2026-10-24T15:20", "문서 검색 데모 후기",
     "신규 입사자 교육에서 반응이 좋았습니다. 검색 결과에 출처가 같이 나오면 더 좋겠다는 의견이 있었습니다."),
    ("jaehwi", ["haeun"], [], "2026-10-24T17:00", "RE: 문서 검색 데모 후기",
     "출처 표시 넣어 보겠습니다. 문서 조각마다 원본 파일 이름을 붙이면 됩니다."),
    ("jiyoung", ["jaehwi"], [], "2026-10-27T09:15", "10월 운영 보고 일정",
     "10월 한 달 운영 보고를 11월 첫 주에 본부장님께 올립니다. 초안을 10/31까지 부탁합니다."),
    ("jaehwi", ["jiyoung"], [], "2026-10-29T18:30", "판교 데이터센터 견학 출장 신청",
     "다음 분기 코로케이션 후보 견학으로 11/5~6 출장 신청 올렸습니다."),
    ("seojun", ["haeun"], [], "2026-10-15T10:00", "평가셋 검수 부탁",
     "평가셋 50문항 중 21~50번 검수 부탁합니다."),
    ("dongho", ["jiyoung", "sora"], [], "2026-10-28T13:00", "4분기 장비 예산",
     "4분기 장비 예산 잔액을 공유합니다. GPU 증설 검토 시 참고하세요."),
    ("haeun", ["seojun"], [], "2026-10-16T16:30", "RE: 평가셋 검수 부탁", "검수 마쳤습니다. 3문항 정답을 고쳤습니다."),
    # ---- 11월 ----
    ("jaehwi", ["jiyoung"], [], "2026-11-09T18:00", "2호기 vLLM 새 버전 설치",
     "새 버전 기동이 실패해서 max-model-len을 32768에서 16384로 낮춰 해결했습니다."),
    ("haeun", ["jaehwi"], [], "2026-11-10T11:20", "문서 검색이 느립니다",
     "오후 2시쯤 검색 응답이 10초 넘게 걸렸습니다. 다른 분들도 같은 문의를 했습니다."),
    ("jaehwi", ["haeun", "jiyoung"], [], "2026-11-10T17:30", "RE: 문서 검색이 느립니다",
     "색인 재생성이 업무 시간에 돌던 것이 원인이었습니다. 재생성을 새벽 2시로 옮겼습니다."),
    ("taeyang", ["jaehwi"], [], "2026-11-12T10:00", "로그 보관 60일 적용 확인",
     "60일 보관 설정이 적용된 것을 확인했습니다."),
    ("seojun", ["jaehwi"], ["jiyoung"], "2026-11-13T15:00", "GPU 증설 견적 검토",
     "구매팀 견적이 오면 현재 사용량 기준으로 1대 추가가 맞는지 같이 보겠습니다."),
    ("jaehwi", ["sora"], [], "2026-11-13T16:00", "GPU 서버 견적 요청",
     "GPU 서버 1대 견적 부탁드립니다. 사양은 2호기와 같습니다."),
]

# (주최, 참석자, 시작, 끝, 제목, 장소, 메모)
EVENTS = [
    ("jiyoung", ["jaehwi", "seojun", "haeun"], "2026-10-01T10:00", "2026-10-01T11:00", "로컬 LLM 도입 킥오프", "3층 회의실", ""),
    ("jaehwi", ["seojun"], "2026-10-06T14:00", "2026-10-06T15:00", "서버·모델 연동 점검", "온라인", ""),
    ("jiyoung", ["jaehwi", "seojun", "haeun"], "2026-10-08T10:00", "2026-10-08T10:30", "주간 팀 회의", "3층 회의실", ""),
    ("jaehwi", ["jiyoung"], "2026-10-09T13:00", "2026-10-09T13:30", "OOM 장애 회고", "온라인", "재발 방지책 정리"),
    ("jaehwi", [], "2026-10-15T09:00", "2026-10-15T18:00", "AI 인프라 세미나 (출장)", "서울", ""),
    ("jiyoung", ["jaehwi", "seojun", "haeun"], "2026-10-15T10:00", "2026-10-15T10:30", "주간 팀 회의", "3층 회의실", ""),
    ("jaehwi", ["haeun", "jiyoung", "seojun"], "2026-10-20T15:00", "2026-10-20T16:00", "문서 검색 데모 (신규 입사자 교육)", "교육장", ""),
    ("taeyang", ["jaehwi"], "2026-10-21T11:00", "2026-10-21T11:30", "로그 보관 기간 협의", "보안팀", ""),
    ("jiyoung", ["jaehwi", "seojun", "haeun"], "2026-10-22T10:00", "2026-10-22T10:30", "주간 팀 회의", "3층 회의실", ""),
    ("dongho", ["jiyoung"], "2026-10-23T14:00", "2026-10-23T15:00", "본부 월간 회의", "본부 회의실", ""),
    ("seojun", ["haeun"], "2026-10-27T13:00", "2026-10-27T14:00", "평가셋 2차 검토", "온라인", ""),
    ("sora", ["dongho"], "2026-10-28T15:00", "2026-10-28T16:00", "4분기 구매 계획", "구매팀", ""),
    # 11월
    ("jaehwi", [], "2026-11-05T09:00", "2026-11-06T18:00", "판교 데이터센터 견학 (출장)", "판교", ""),
    ("jiyoung", ["jaehwi", "seojun", "haeun"], "2026-11-12T10:00", "2026-11-12T10:30", "주간 팀 회의", "3층 회의실", ""),
    ("taeyang", ["jaehwi"], "2026-11-12T14:00", "2026-11-12T14:30", "로그 보관 60일 적용 점검", "온라인", ""),
    ("seojun", ["jaehwi"], "2026-11-13T15:00", "2026-11-13T16:00", "GPU 증설 견적 검토", "3층 회의실", ""),
]

# (기안, 종류, 제목, 본문, 결재선, 기안 시각, [(결재자, 결정, 의견, 시각)])
APPROVALS = [
    ("jaehwi", "출장신청", "AI 인프라 세미나 참석 출장", "10/15 서울, 서빙 엔진 운영 사례 수집",
     ["jiyoung"], "2026-10-13T16:00", [("jiyoung", "approve", "", "2026-10-13T17:10")]),
    ("jaehwi", "구매품의", "GPU 모니터링 라이선스 구매", "GPU 메모리·온도 모니터링 SaaS 1년 라이선스, 2대분",
     ["jiyoung", "dongho"], "2026-10-14T11:00",
     [("jiyoung", "approve", "", "2026-10-14T13:00"), ("dongho", "approve", "", "2026-10-15T09:30")]),
    ("jaehwi", "출장보고", "AI 인프라 세미나 출장 결과 보고", "배칭 설정·KV 캐시 관리 사례, 적용 검토 항목 3건",
     ["jiyoung"], "2026-10-16T10:00", [("jiyoung", "approve", "잘 정리했습니다", "2026-10-16T15:00")]),
    ("jaehwi", "보안예외", "LLM 요청 로그 90일 보관 예외 신청", "장애 분석을 위해 요청 로그를 90일 보관",
     ["jiyoung", "taeyang"], "2026-10-20T11:00",
     [("jiyoung", "approve", "", "2026-10-20T14:00"),
      ("taeyang", "reject", "개인정보 포함 가능성. 60일 이하로 재신청", "2026-10-22T16:00")]),
    ("jaehwi", "보안예외", "LLM 요청 로그 60일 보관 예외 재신청", "보안팀 의견 반영, 보관 기간 60일",
     ["jiyoung", "taeyang"], "2026-10-24T10:00",
     [("jiyoung", "approve", "", "2026-10-24T11:00"), ("taeyang", "approve", "", "2026-10-28T10:00")]),
    ("jaehwi", "출장신청", "판교 데이터센터 견학 출장", "11/5~6 판교, 코로케이션 후보 견학",
     ["jiyoung"], "2026-10-29T18:00", [("jiyoung", "approve", "", "2026-10-30T09:00")]),
]


def seed(dsn: str = db.DSN, schema: str = db.SCHEMA, *, reset: bool = True) -> dict:
    """스키마를 (지우고) 만들고 샘플 데이터를 넣는다. 테이블별 행 수를 돌려준다."""
    db.migrate(dsn, schema, reset=reset)
    store = db.Store(dsn, schema, max_size=1)
    try:
        with store.tx() as c:
            c.cursor().executemany("INSERT INTO users(id,name,team,title) VALUES(%s,%s,%s,%s)", USERS)
            for s, to, cc, at, subj, body in MAILS:
                mid = c.execute("INSERT INTO mails(sender,subject,body,sent_at) VALUES(%s,%s,%s,%s) RETURNING id",
                                (s, subj, body, at + ":00")).fetchone()["id"]
                add_recipients(c, mid, to, cc, read_at=at + ":00")
            for org, att, st, en, title, loc, memo in EVENTS:
                eid = c.execute("INSERT INTO events(organizer,title,start_at,end_at,location,memo)"
                                " VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                                (org, title, st + ":00", en + ":00", loc, memo)).fetchone()["id"]
                add_attendees(c, eid, att)
            for drafter, kind, title, body, line, at, decisions in APPROVALS:
                rejected = any(d == "reject" for _, d, _, _ in decisions)
                status = "rejected" if rejected else ("approved" if len(decisions) == len(line) else "pending")
                decided = decisions[-1][3] + ":00" if status != "pending" else None
                aid = c.execute("INSERT INTO approvals(drafter,kind,title,body,status,step,created_at,decided_at)"
                                " VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                                (drafter, kind, title, body, status, len(decisions), at + ":00", decided)).fetchone()["id"]
                add_line(c, aid, line)
                for seq, (by, d, comment, t) in enumerate(decisions):
                    c.execute("UPDATE approval_lines SET decision=%s, comment=%s, decided_at=%s"
                              " WHERE approval_id=%s AND seq=%s AND approver=%s", (d, comment, t + ":00", aid, seq, by))
        return {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                for t in ("users", "mails", "events", "approvals")}
    finally:
        store.close()


def add_recipients(c, mail_id: int, to: list[str], cc: list[str], read_at: str | None = None) -> None:
    """받는 사람·참조. 샘플 데이터는 과거 메일이라 모두 읽음으로 둔다(read_at)."""
    rows = [(mail_id, u, "to", i, read_at) for i, u in enumerate(to)]
    rows += [(mail_id, u, "cc", len(to) + i, read_at) for i, u in enumerate(cc)]
    if rows:
        c.cursor().executemany("INSERT INTO mail_recipients(mail_id,user_id,kind,seq,read_at) VALUES(%s,%s,%s,%s,%s)"
                               " ON CONFLICT DO NOTHING", rows)


def add_attendees(c, event_id: int, users: list[str]) -> None:
    rows = [(event_id, u, i) for i, u in enumerate(dict.fromkeys(users))]
    if rows:
        c.cursor().executemany("INSERT INTO event_attendees(event_id,user_id,seq) VALUES(%s,%s,%s)", rows)


def add_line(c, approval_id: int, line: list[str]) -> None:
    c.cursor().executemany("INSERT INTO approval_lines(approval_id,seq,approver) VALUES(%s,%s,%s)",
                           [(approval_id, i, u) for i, u in enumerate(line)])


if __name__ == "__main__":
    print(db.SCHEMA, seed())
