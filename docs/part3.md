# 3편 실습 코드

「내 컴퓨터로 AGENT 개발하기」 3편: 보고서 Agent 하나를 LangGraph로 Single → Workflow → Multi-Agent → Graph로 키운 코드. 4편 서비스 안에서도 그대로 돈다(비서 패널 ⚙ "실행할 설계"에서 고르거나 http://127.0.0.1:8200/chat).

| 폴더 | 내용 |
|---|---|
| `s1_single/` | 기본 Tool Calling, ReAct, Conditional Branch, Retry/Fallback/Stop, Reflection |
| `s2_workflow/` | RAG 도구만 붙인 Single(먼저), Prompt Chaining, Routing, Parallelization, Orchestrator–Worker, Evaluator–Optimizer |
| `s3_multi/` | 도구 12개 Single(먼저), Supervisor/Worker, Handoff, Hierarchical, Sequential/Parallel, 상태·결과 공유 |
| `s4_graph/` | DAG/Cyclic, Dynamic Routing, Planner–Executor·Replanning, Human-in-the-loop, 전체 그래프 |
| `common/` | 모델 연결(`llm.py`, KANANA 호출 해석), 도구, 보고서 저장, 실행 로그 |

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe s1_single\01_tool_calling.py
.\.venv\Scripts\python.exe s4_graph\05_full_graph.py
```

2단계부터는 그룹웨어(8100)가 떠 있어야 한다. 실행 로그는 `blog/logs/`, docx는 `output/`.

## MSDF (데이터 준비)

문서 검색과 그룹웨어 수집은 MSDF(Micro Service Data Flow, https://github.com/funpinbo/MSDF )에 결과물 이름으로 요청한다(`infra/msdf/`).

- `MSDF_MODE=local`(기본): 엔진이 Agent 프로세스 안에서 돈다. 그룹웨어만 띄우면 된다.
- `MSDF_MODE=remote`: `.\run_backend.ps1`로 그룹웨어 + `msdf up` + 서비스를 띄운다. MSDF UI http://127.0.0.1:8787/ui
