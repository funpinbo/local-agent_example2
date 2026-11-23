# 업무 비서 서버

`chat_ui/server.py` (FastAPI, 8200). 그룹웨어 화면의 비서 패널이 부르는 서버이고, 3편 챗봇 화면(`/chat`)도 여기에 있다.
그룹웨어와 출처(포트)가 달라 CORS로 `127.0.0.1`·`localhost`의 모든 포트를 허용한다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/chat/api/catalog` | 실행할 수 있는 것 목록(업무 비서 + 3편 실습 23개)과 선택지 |
| POST | `/chat/api/run` | 실행 시작. `{key, text, user, stage, skills, …}` → 작업 |
| GET | `/chat/api/run/{id}?since=n` | 폴링: n번째 이후 이벤트와 상태 |
| GET | `/chat/api/run/{id}/events` | **SSE**: 이벤트마다 `event: step`(id = 순번), 상태가 바뀌면 `event: status`. done/error에서 닫는다. 결정 대기 중에도 열려 있다. `Last-Event-ID`로 이어 받기, 15초마다 keep-alive |
| POST | `/chat/api/run/{id}/resume` | 사람의 결정 `{action: approve\|reject\|edit, args?, comment?, title?}` |
| GET | `/chat/api/mcp` | MCP 서버 상태, 연결할 때 오간 메시지 |
| POST | `/chat/api/mcp/connect` | MCP 서버에 지금 붙기 |
| GET/POST/DELETE | `/chat/api/uploads` | 문서 질문용 문서 올리기(MSDF 색인) |
| GET | `/chat/files/{name}` | 비서가 만든 docx 내려받기 |

## 업무 비서 (`key: "assistant"`)

| 입력 | 뜻 |
|---|---|
| `text` | 질문 |
| `user` | 그룹웨어 사용자. 그룹웨어 MCP 호출에 "누구 대신"으로 실리고 시스템 프롬프트 이름이 된다 |
| `stage` | 장별 도구 구성. `1` 비서 코드 안의 도구만(3편 그룹웨어 읽기 10 + 문서 검색) / `2` + 기성 MCP(time·filesystem·notion) / `3` + 그룹웨어 MCP(비서 코드의 그룹웨어 도구는 빠진다) / `5` 3 + Skill (기본) |
| `skills` | `on`/`off`. 주면 `stage`보다 우선 |

쓰기 도구(그룹웨어 MCP의 메일·일정·결재)가 호출되면 실행이 `waiting`이 되고 `interrupt`에 `{kind: "tool", tool, server, args, description}`이 실린다. `resume`으로 승인·거절·인자 수정.

## 이벤트 종류 (타임라인)

| type | 뜻 |
|---|---|
| `state` | 설정·상태 (장별 상태 등) |
| `mcp_status` | MCP 서버 상태 |
| `skills` / `skill` | Skill 목록 / Skill을 불러옴(도구 수 변화) |
| `tools` | 이번에 붙인 도구와 출처(`local`, `mcp:<서버>`, `skill`), JSON Schema |
| `llm_raw` | 모델 호출 한 번: 프롬프트 전체, 이번에 붙은 글자, 모델이 쓴 글자, 토큰 수, 첫 토큰 후보, HTTP 요청 |
| `parsed` | 파서 판정과 호출 JSON |
| `mcp` | MCP JSON-RPC 메시지(보냄/받음) 원문 |
| `approval` | 사람의 결정(승인/거절, 고친 인자) |
| `tool` | 도구 실행 결과 |
| `interrupt` / `human` / `error` | 결정 대기 / 사람 입력 / 오류 |

3편 실습(`s1_01` … `s4_05`, `chat_qa`)은 `ai`, `tool`, `state`, `msdf` 이벤트를 낸다.

## 비서 패널 (`web/src/assistant/`)

채팅, 과정 타임라인(위 이벤트를 펼쳐 보기), 답과 근거 표시, docx 내려받기, 승인 카드(쓰기 도구: 인자 JSON을 고쳐 실행 / 의견과 함께 거절, 3편 Human-in-the-loop: 상신 승인), ⚙ 설정(실행할 설계, 장별 상태, SSE/폴링, MCP 서버 목록과 연결, 문서 올리기), 패널 넓게.
