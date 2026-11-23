# 설치와 실행 (Windows 11, PowerShell)

## 필요한 것

| 무엇 | 확인한 버전 | 비고 |
|---|---|---|
| Python | 3.12 | |
| Node.js | 22 | 웹 화면 빌드 |
| Docker Desktop | 28 | PostgreSQL |
| Ollama | 0.40 | 모델 서빙 |
| GPU | RTX 5060 Ti 16GB | Q8_0 + 컨텍스트 16k가 약 10GB |

테스트는 GPU·Ollama 없이 돈다(가짜 모델). PostgreSQL은 필요하다.

## 1. 설치

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m common.templates.make_templates      # 3편 docx 양식
cd web; npm install; npm run build; cd ..                          # 그룹웨어 화면
copy .env.example .env                                             # 값 채우기 (아래)
```

`.env`
- `GROUPWARE_MCP_KEY` — 비서가 그룹웨어 MCP 서버를 부를 때 쓰는 서비스 키. 아무 긴 문자열.
- `NOTION_TOKEN` — (선택) Notion MCP. 없으면 Notion 서버는 "설정 필요"로 건너뛴다.

## 2. 모델

[serving.md](serving.md) 순서대로 GGUF를 만들고 등록한다.

```powershell
ollama create kanana-1.5-8b-q8 -f serving\Modelfile.q8
```

## 3. DB와 샘플 데이터

```powershell
docker compose -f infra/docker-compose.yml up -d
.\.venv\Scripts\python.exe -m infra.groupware.seed        # groupware 스키마를 지우고 다시 만든다
```

샘플 데이터는 AI로 만든 가상 회사 "놀러가자"의 2026년 10~11월 메일·일정·결재다. 11월 결재는 3편 실습(재계획)을 위해 일부러 비워 두었다. 비서로 메일을 보내거나 결재를 올렸다면 이 명령으로 되돌린다.

## 4. 서버 띄우기 (각각 다른 창)

```powershell
.\.venv\Scripts\python.exe -m uvicorn infra.groupware.app:app --port 8100
.\.venv\Scripts\python.exe -m uvicorn chat_ui.server:app --port 8200
.\.venv\Scripts\python.exe -m chat_ui.mcp_servers.groupware
```

http://127.0.0.1:8100 을 열면 그룹웨어 화면 오른쪽에 비서 패널이 있다. 오른쪽 위에서 사용자를 고른다(로그인 대신).
기성 MCP 서버(time, filesystem, notion)는 비서가 첫 질문 때 띄운다. filesystem·notion은 `npx`로 내려받으므로 처음 한 번은 오래 걸린다.

## 환경 변수

| 이름 | 기본값 | |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `LLM_MODEL` | `kanana-1.5-8b-q8` | |
| `LLM_TEMPERATURE` | `0.2` | |
| `LLM_NUM_CTX` | `16384` | |
| `LLM_NUM_PREDICT` | `2048` | 출력 길이 상한 |
| `GROUPWARE_URL` | `http://127.0.0.1:8100` | 비서·MCP 서버가 부르는 그룹웨어 |
| `GROUPWARE_DSN` | `postgresql://groupware:groupware@127.0.0.1:15432/office` | |
| `GROUPWARE_SCHEMA` | `groupware` | |
| `AGENT_DSN` | `postgresql://assistant:assistant@127.0.0.1:15432/office` | 실행 기록 |
| `AGENT_SCHEMA` | `agent` | |
| `GROUPWARE_MCP_KEY` | (.env) | |
| `NOTION_TOKEN` | (.env) | |
| `MSDF_MODE` | `local` | 3편 데이터 엔진. [part3.md](part3.md) |
| `VITE_AGENT_URL` | `http://<현재 호스트>:8200` | 웹 화면 빌드 때 비서 주소 |

DB 비밀번호는 로컬 실습용이다(`infra/db/init/00_roles.sql`). 외부에 열지 않는다(compose는 127.0.0.1에만 묶는다).

## 문제가 생기면

- **Docker가 안 뜬다** (`dockerInference … cannot be accessed`) — `%LOCALAPPDATA%\Docker\run`에 남은 소켓 파일 문제. Docker Desktop을 끄고 이 폴더 이름을 바꾼 뒤 다시 켠다.
- **5432 포트를 쓸 수 없다** — 다른 PostgreSQL이 쓰고 있다. 이 프로젝트는 15432를 쓴다.
- **`npx` 실행 오류 (WinError 193)** — 비서가 `npx.cmd`를 찾아 쓴다. Node가 PATH에 있는지 확인.
- **비서 패널이 "붙지 못했습니다"** — 8200 서버가 떠 있는지, 그룹웨어 화면을 다른 호스트 이름으로 열었다면 `VITE_AGENT_URL`.
