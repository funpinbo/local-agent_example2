# 실습용 그룹웨어

가상 회사 "놀러가자"의 메일·일정·결재 서버. 비서가 일할 바깥 시스템 역할이다.
`infra/groupware/app.py`(FastAPI), 저장소는 PostgreSQL `groupware` 스키마, 화면은 `web/`(React) 빌드를 `/`에 붙인다.

```powershell
.\.venv\Scripts\python.exe -m infra.groupware.seed
.\.venv\Scripts\python.exe -m uvicorn infra.groupware.app:app --port 8100
```

## 인증

실습용이라 로그인 대신 `X-User` 헤더(사용자 아이디)로 사람을 구분한다. 없으면 401, 없는 사용자면 403.
사용자: `jaehwi`(이재휘, AI플랫폼팀), `jiyoung`(박지영 팀장), `seojun`, `haeun`, `dongho`(본부장), `sora`(구매팀), `taeyang`(보안팀).

## REST API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/users` | 사용자 목록 (헤더 없이) |
| GET | `/api/summary` | 안 읽은 메일 수, 내 차례 결재 수, 마지막 변경 이벤트 id |
| GET | `/api/notifications?after=&limit=` | 나와 관련된 변경 이벤트 |
| GET | `/api/mails?box=inbox\|sent&since=&until=&q=` | 메일 목록. `read`(읽음 여부) 포함 |
| GET | `/api/mails/{id}` | 메일 하나 |
| POST | `/api/mails/{id}/read` | 읽음 표시 |
| POST | `/api/mails` | 보내기 `{to, cc, subject, body}` |
| GET | `/api/events?since=&until=` | 내가 주최·참석하는 일정 |
| GET | `/api/events/{id}` | 일정 하나 |
| POST | `/api/events` | 만들기 `{title, start, end, location, attendees, memo}` |
| GET | `/api/approvals?role=drafter\|approver\|todo&status=&since=&until=` | 결재 목록. `todo` = 지금 내 차례 |
| GET | `/api/approvals/{id}` | 결재 하나 (결재선, 이력) |
| POST | `/api/approvals` | 올리기 `{kind, title, body, line, attachment_b64?, attachment_name?}` |
| POST | `/api/approvals/{id}/decide` | 승인·반려 `{decision: approve\|reject, comment}`. 차례가 아니면 403 |
| GET | `/api/approvals/{id}/attachment` | 첨부 내려받기 |
| POST | `/api/webhooks` | 변경 알림을 받을 URL 등록 `{url}` |

시각은 `2026-10-01T09:10:00` 같은 문자열. `since`/`until`은 `until` 미만.

## 변경 이벤트

메일·일정·결재를 바꾸는 트랜잭션 안에서 `outbox_events`에 한 행을 넣고 `NOTIFY groupware_events, '<id>'`를 보낸다(커밋될 때 전달). 커밋 뒤에는 등록된 webhook으로도 보낸다(3편 MSDF 수집이 이것을 듣는다). 화면의 알림 종은 `/api/notifications`를 읽는다.

## 화면 (`web/`)

React 19 + Vite 8 + TypeScript. 메일함(받은/보낸, 읽음, 검색, 답장, 쓰기), 월 달력(만들기, 상세), 전자결재(내 차례/올린 문서/결재선에 든 문서, 결재선 진행 표시, 승인·반려·의견, 기안·첨부), 알림 종, 오른쪽 비서 패널.

```powershell
cd web
npm run dev        # 5173, /api → 8100
npm run build      # web/dist → 그룹웨어 서버가 / 에 붙인다
```

화면은 10초마다 `/api/summary`를 물어 새 변경이 있으면 다시 읽는다.
