# MCP

## MCP 호스트 (`chat_ui/agent/mcp_host.py`)

비서가 여러 MCP 서버에 붙어 도구를 받아 오고 부른다. `mcp` Python SDK 2.x, 스펙 2026-07-28(옛 서버는 SDK가 `initialize` 핸드셰이크로 되돌아가 붙는다).

- 서버마다 연결을 하나씩 길게 열어 둔다(stdio면 자식 프로세스가 계속 떠 있다). MCP 세션은 연 작업에서 닫아야 해서 서버마다 asyncio 작업 하나 + 요청 큐를 두고, 동기 코드(비서 루프)는 큐로 요청한다.
- 전송을 한 겹 감싸(Tap) 보내고 받은 JSON-RPC 메시지를 그대로 이벤트로 낸다 → 패널 타임라인, `agent.mcp_messages`.
- 서버 도구는 `Tool(source="mcp:<서버>")`로 바뀌어 비서 코드의 도구와 같은 프롬프트에 들어간다.
- Windows에서 `npx`는 셸 스크립트라 그대로 실행이 안 된다(WinError 193) → `npx.cmd`를 찾아 쓴다.

### `chat_ui/agent/servers.yaml`

```yaml
servers:
  groupware:
    transport: http
    url: http://127.0.0.1:8301/mcp
    headers:
      Authorization: "Bearer ${GROUPWARE_MCP_KEY}"   # .env
    pass_user: true            # 요청마다 _meta["local.office/user"] = 지금 사용자
  time:
    transport: stdio
    command: ${PYTHON}
    args: ["-m", "mcp_server_time", "--local-timezone", "Asia/Seoul"]
    read_only: true
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem@2.3.0", "${ROOT}/output"]
    read_only: true
    tools: [list_directory, read_text_file, search_files, get_file_info]
  notion:
    transport: stdio
    command: npx
    args: ["-y", "@notionhq/notion-mcp-server@2.6.1"]
    env: {NOTION_TOKEN: ${NOTION_TOKEN}}
    read_only: true
    verified_read: [API-post-search, API-query-data-source]
    tools: [API-post-search, API-query-data-source, API-retrieve-page-markdown]
```

| 키 | 뜻 |
|---|---|
| `${이름}` | 환경 변수 또는 `.env`. `${PYTHON}` = 지금 파이썬, `${ROOT}` = 프로젝트 폴더. 값이 없으면 그 서버는 `needs_env`로 건너뛴다 |
| `read_only` | 서버가 `readOnlyHint`를 단 도구만 붙인다 |
| `verified_read` | 서버 표시와 달리 우리가 읽기라고 확인한 도구 (Notion 서버는 HTTP POST면 모두 "쓰기"로 표시한다) |
| `tools` | 그중 이것만 붙인다 (도구가 늘면 프롬프트가 커지고 8B가 고르기 어려워진다) |
| `pass_user` | 요청 `_meta`에 지금 사용자를 싣는다 |

annotation은 스펙상 "힌트"이고 믿을 수 없는 서버의 것은 판단에 쓰지 말라고 되어 있다. 그래서 허용 목록을 같이 둔다.

## 그룹웨어 MCP 서버 (`chat_ui/mcp_servers/groupware/`)

```powershell
.\.venv\Scripts\python.exe -m chat_ui.mcp_servers.groupware        # http://127.0.0.1:8301/mcp
```

`MCPServer`, Streamable HTTP(stateless). 안에서 그룹웨어 REST를 부른다.

| 도구 | 종류 | |
|---|---|---|
| `find_mails(query, since, until, box, limit)` | 읽기 | 기간은 날짜 범위(until 포함), 결과에 ref·이름(팀)·앞부분 |
| `read_mail(mail_id)` | 읽기 | |
| `find_events(since, until, query)` | 읽기 | |
| `find_approvals(query, status, since, until, role)` | 읽기 | query는 제목·본문·종류·사람 이름·팀·결재 의견. 마지막 결재 의견(반려 사유)까지 |
| `read_approval(approval_id)` | 읽기 | 이력 전체 |
| `people(query)` | 읽기 | |
| `send_mail(to, subject, body, cc)` | 쓰기 | 이름이나 아이디 |
| `create_event(title, start, end, attendees, location, memo)` | 쓰기 | |
| `submit_approval(kind, title, body, line)` | 쓰기 | |
| `decide_approval(approval_id, decision, comment)` | 쓰기 | `destructiveHint` |

리소스 `gw://people`, 프롬프트 `monthly_report(month)`.

**인증(실습용 단순화)**
- 서비스 키: `Authorization: Bearer $GROUPWARE_MCP_KEY` — 없거나 틀리면 401. 누가 이 서버를 부를 수 있나.
- 사용자: 요청 `_meta["local.office/user"]` — 누구 대신 부르나. 서버가 그룹웨어 `X-User`로 바꿔 부른다. 실제 서비스라면 사용자별 OAuth 위임 토큰이 들어갈 자리다.

**오류**: 일반 예외는 SDK가 "Error executing tool …"으로 가린다. 모델이 읽고 고칠 수 있게 `ToolError`로 글을 넘긴다.

**쓰기 승인은 서버가 아니라 비서(클라이언트)가 한다** — 서버는 `readOnlyHint=false`만 단다.

SDK 메모: Python SDK 2.x의 `streamable_http_client`에는 `headers` 인자가 없다. `create_mcp_http_client(headers=…)`로 만든 HTTP 클라이언트를 넘긴다.
