# 데이터베이스

PostgreSQL 18 + pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg18`), Docker. DB `office`, 포트 15432.

```powershell
docker compose -f infra/docker-compose.yml up -d
```

## 계정과 스키마 (`infra/db/init/00_roles.sql`, 컨테이너를 처음 만들 때 한 번)

| 계정 | 스키마 | 쓰는 쪽 |
|---|---|---|
| `groupware` | `groupware` | 그룹웨어 서버만 |
| `assistant` | `agent`, `docs` | 업무 비서 (그룹웨어 스키마 권한 없음) |
| `postgres` | 전체 | 관리 |

비서가 그룹웨어 데이터를 보려면 반드시 그룹웨어 API(→ MCP 서버)를 거친다. 이 경계를 코드가 아니라 DB 권한으로 지킨다. 두 계정 모두 데이터베이스에 스키마를 만들 수 있다(테스트는 임시 스키마를 만들고 지운다).

## 마이그레이션

`infra/db/migrations/<스키마>/NNN_*.sql`을 순서대로 적용하고 `schema_migrations`에 기록한다(`infra/groupware/db.py`의 `migrate`). 그룹웨어는 `seed`가, 비서 기록은 첫 실행 때 적용한다.

### `groupware`

`users`, `mails` + `mail_recipients`(받는 사람·참조·읽음), `events` + `event_attendees`, `approvals` + `approval_lines`(결재선 한 칸 = 한 행, 결정이 채워진 행이 곧 이력) + `approval_attachments`, `webhooks`, `outbox_events`(변경 이벤트).

### `agent` — 비서 실행 기록

| 테이블 | 한 행 |
|---|---|
| `runs` | 실행 하나 (질문, 사용자, 모델, 멈춘 이유, 답) |
| `llm_calls` | 모델 호출 한 번: **모델이 본 프롬프트 원문, 쓴 글자 원문**, 토큰 수, 시간, 첫 토큰 후보, HTTP 요청 |
| `tool_calls` | 도구 호출: 이름, 인자, 결과, 성공 여부, 시간 |
| `mcp_messages` | MCP JSON-RPC 메시지 원문 (보냄/받음) |

## 조회 예

```powershell
docker exec -it local-agent-postgres-1 psql -U assistant -d office
```

```sql
SET search_path TO agent;
SELECT id, left(input, 40), stop_reason, started_at FROM runs ORDER BY started_at DESC LIMIT 20;
SELECT seq, prompt_tokens, output_tokens, ms, first_token->0->>'token', left(output, 80)
  FROM llm_calls WHERE run_id = '<id>' ORDER BY seq;
SELECT seq, name, args, ok, left(result, 100) FROM tool_calls WHERE run_id = '<id>' ORDER BY seq;
SELECT server, direction, method, rpc_id, bytes FROM mcp_messages WHERE run_id = '<id>' ORDER BY id;
```

관리 화면: Adminer http://127.0.0.1:8081 (서버 `postgres`).
