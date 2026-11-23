# local-agent example 2 — 그룹웨어에 붙은 업무 비서

블로그 「내 컴퓨터로 AGENT 개발하기」 4편의 예시 코드입니다.

3편에서 한 프로세스 안에서 키운 보고서 Agent를, 그룹웨어 화면에 붙은 **업무 비서 서비스**로 키웠습니다. 여러 서버가 통신하는 구조 위에서 Agent가 바깥과 이어지는 방식을 하나씩 만들어 봅니다.

1. **Tool Calling** — 원본 chat template을 직접 렌더링해 Ollama raw 모드로 보내고, 모델이 본 글자와 쓴 글자를 그대로 남긴다
2. **MCP 붙이기** — time · filesystem · Notion 서버를 stdio로
3. **MCP 서버 만들기** — 그룹웨어 MCP 서버(Streamable HTTP, 서비스 키 + 사용자 위임), 쓰기 도구는 사람 승인
4. **Skills** — 절차서를 필요할 때만 불러오고, 불러오면 도구를 그 일에 필요한 것만 남긴다

모델은 로컬에서 돌립니다: KANANA 1.5 8B Instruct를 직접 Q8_0으로 양자화해 Ollama로(RTX 5060 Ti 16GB).

```
브라우저(그룹웨어 화면 + 비서 패널)
   │ REST                   │ POST + SSE
   ▼                        ▼
그룹웨어 :8100          업무 비서 :8200 ── Ollama :11434 (KANANA 1.5 8B)
   ▲   │                    │  ├─ MCP(HTTP) ── 그룹웨어 MCP :8301 ── REST ──▶ 그룹웨어
   │   ▼                    │  └─ MCP(stdio) ── time · filesystem · notion
   │  PostgreSQL :15432 ◀───┘ 실행 기록(프롬프트·출력·도구 호출·MCP 메시지)
```

## 빠른 시작 (Windows, PowerShell)

필요한 것: Python 3.12, Node 22, Docker, Ollama, (모델용) 16GB GPU. 자세한 것은 [docs/setup.md](docs/setup.md).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd web; npm install; npm run build; cd ..
copy .env.example .env            # GROUPWARE_MCP_KEY 채우기 (NOTION_TOKEN은 선택)

docker compose -f infra/docker-compose.yml up -d
.\.venv\Scripts\python.exe -m infra.groupware.seed
ollama create kanana-1.5-8b-q8 -f serving\Modelfile.q8   # GGUF 만드는 법: docs/serving.md
```

서버 세 개를 각각 띄운다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn infra.groupware.app:app --port 8100
.\.venv\Scripts\python.exe -m uvicorn chat_ui.server:app --port 8200
.\.venv\Scripts\python.exe -m chat_ui.mcp_servers.groupware
```

http://127.0.0.1:8100 — 오른쪽 위에서 사용자를 고르고(로그인 대신), 오른쪽 비서 패널에 질문한다. 패널 ⚙에서 장별 상태(1·2·3·5장 도구 구성), SSE/폴링, MCP 서버 연결을 바꿀 수 있다.

테스트(GPU 없이, PostgreSQL 필요):

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 문서

| | |
|---|---|
| [구조](docs/architecture.md) | 서비스, 포트, 통신 경로, 폴더 |
| [설치와 실행](docs/setup.md) | 환경 변수, 문제 해결 |
| [모델 서빙](docs/serving.md) | 양자화, Ollama, 도구 호출 형식 |
| [그룹웨어](docs/groupware.md) | REST API, 화면, 변경 이벤트 |
| [업무 비서 서버](docs/assistant.md) | API, SSE, 장별 상태, 이벤트 종류 |
| [Tool Calling 층](docs/tool-calling.md) | 렌더링, raw 모드, 파서, 실행 루프, 쓰기 승인 |
| [MCP](docs/mcp.md) | 호스트와 servers.yaml, 그룹웨어 MCP 서버 |
| [Skills](docs/skills.md) | SKILL.md 형식과 동작 |
| [데이터베이스](docs/database.md) | 스키마, 계정, 실행 기록 조회 |
| [테스트와 실습 스크립트](docs/testing.md) | pytest, 장별 시나리오 |
| [3편 실습 코드](docs/part3.md) | s1~s4, MSDF |

## 참고

- 샘플 데이터(가상 회사 "놀러가자"의 메일·일정·결재, 사내 문서)는 AI로 만든 가상 데이터입니다.
- 인증은 실습용으로 단순화했습니다(그룹웨어는 `X-User` 헤더, MCP 서버는 서비스 키 + 요청 `_meta`의 사용자). 외부에 열지 마세요.
- 3편 예시 코드: https://github.com/funpinbo/local-agent_example1
- 데이터 엔진 MSDF: https://github.com/funpinbo/MSDF
