# 테스트와 실습 스크립트

## pytest

```powershell
.\.venv\Scripts\python.exe -m pytest
```

GPU·Ollama 없이 가짜 모델로 돈다. **PostgreSQL은 떠 있어야 한다** — 테스트마다 임시 스키마(`gw_test_…`, `agent_test_…`)를 만들고 끝나면 지운다.

| 파일 | 무엇 |
|---|---|
| `test_groupware.py` | 그룹웨어 API (기간 필터, 결재 차례, 첨부 권한, 변경 이벤트) |
| `test_chat_ui.py` | 비서 서버 API, SSE(이어 받기 포함), CORS, 3편 실습 실행 |
| `test_toolcall.py` | 템플릿 렌더링, 결과가 붙는 모양, 틀린 호출 돌려주기, 멈춤 조건, 실행 기록 |
| `test_mcp_host.py` | 테스트용 stdio 서버(`mcp_fake_server.py`)에 실제로 붙기, JSON-RPC 엿보기, 도구 고르기 |
| `test_groupware_mcp.py` | 그룹웨어 MCP 서버(같은 프로세스/HTTP), 서비스 키, 쓰기 승인·거절·인자 수정, 장별 상태 |
| `test_skills.py` | Skill 파일, 불러오면 도구가 줄고 프롬프트가 바뀌는지 |
| `test_s1_single.py` … `test_s4_graph.py`, `test_backend.py` | 3편 실습 |

기성 MCP 서버(npx)는 테스트에서 띄우지 않는다(`conftest.py`가 끈다).

## 실습 스크립트 (실제 모델)

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe steps\p1_tool_calling\01_prompt_anatomy.py
.\.venv\Scripts\python.exe steps\p1_tool_calling\02_first_token.py
.\.venv\Scripts\python.exe steps\p4_scenarios.py --list
.\.venv\Scripts\python.exe steps\p4_scenarios.py --chapter 3
```

`steps/p4_scenarios.yaml`에 장별 질문(장별 상태, 사용자, 쓰기 도구를 승인할지 거절할지)을 적어 두었다. 스크립트는 비서 서버(8200)를 불러 실행하고, 결과를 `blog/logs/p4_<id>_<시각>.json`에 남긴다. 쓰기 시나리오 뒤에는 샘플 데이터를 다시 넣는다(`--keep-data`로 끈다). 같은 질문도 실행마다 결과가 달라질 수 있다.
