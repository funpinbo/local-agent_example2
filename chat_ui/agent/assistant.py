"""업무 비서. Tool Calling 층(toolcall.py) 위에 도구를 붙이고 루프 하나로 돈다.

- 1장: 도구는 3편에서 쓰던 함수(그룹웨어 읽기 10개 + 사내 문서 검색 1개)가 비서 코드 안에 있다(local).
- 2장: 여기에 MCP 서버가 준 도구가 더해진다(servers.yaml — time, filesystem, notion).
  모델이 보는 모양은 같다. 실행만 다른 프로세스로 JSON-RPC를 보내 한다.
- 3장: 그룹웨어 도구가 직접 만든 MCP 서버(groupware)로 나갔다. 그 서버가 붙어 있으면 비서 코드 안의
  그룹웨어 도구는 빼고(문서 검색만 남김), 쓰기 도구는 실행 전에 사람 승인을 받는다(toolcall.run_loop의 approve).
- 5장: Skill을 켜면 평소에는 Skill 한 줄 목록 + 기본 도구(비서 코드·그룹웨어·시간)만 싣고,
  load_skill로 절차를 불러오면 그 Skill의 allowed-tools만 남긴다(skills.py).
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from chat_ui.agent import skills as skills_mod
from chat_ui.agent import toolcall

# Skill을 켰을 때 Skill 없이도 붙여 두는 도구의 출처. Notion·filesystem은 Skill을 불러야 보인다.
BASE_SOURCES = {"local", "mcp:groupware", "mcp:time"}
from common import doc_tools, gw_tools

SYSTEM = """너는 놀러가자 회사 {user_name}({user})의 업무 비서다. 오늘은 {today}이다.
그룹웨어(메일·일정·결재)와 사내 문서를 도구로 찾아보고 한국어로 답한다.
- 필요한 도구를 하나씩 부르고, 결과를 본 뒤 다음 도구를 정한다.
- 메일 보내기·일정 만들기·결재 올리기/결정은 실행 전에 사용자가 승인한다. 사용자가 부탁했을 때만 부른다.
- 답의 문장 끝에는 도구 결과에 나온 근거 id를 대괄호로 단다. 예: [mail:12], [approval:3], [03_oom.md#2]
- 도구 결과에 없는 내용은 지어내지 말고 모른다고 답한다."""


def local_tools() -> list[toolcall.Tool]:
    return [toolcall.from_langchain(t) for t in [*gw_tools.DATA_TOOLS, doc_tools.doc_search]]


def tools(host=None, exclude_servers: set[str] | None = None) -> list[toolcall.Tool]:
    """비서 코드 안의 도구 + 연결된 MCP 서버의 도구. 이름이 겹치면 MCP 쪽에 '서버_' 접두어를 붙인다.
    그룹웨어 MCP 서버가 붙어 있으면 비서 코드 안의 그룹웨어 도구(3편 gw_tools)는 뺀다.
    exclude_servers: 이 MCP 서버의 도구는 붙이지 않는다(장별 상태 재현용, 예: 2장은 groupware 없이)."""
    ex = {f"mcp:{s}" for s in exclude_servers or ()}
    mcp_tools = [t for t in host.tools() if t.source not in ex] if host is not None else []
    out = local_tools()
    if any(t.source == "mcp:groupware" for t in mcp_tools):
        out = [t for t in out if not t.name.startswith("gw_")]
    if host is not None:
        names = {t.name for t in out}
        for t in mcp_tools:
            if t.name in names:
                t = toolcall.Tool(f"{t.source[4:]}_{t.name}", t.description, t.parameters, t.fn, t.writes, t.source)
            names.add(t.name)
            out.append(t)
    return out


def messages_for(question: str, user: str = "jaehwi", user_name: str = "이재휘", extra_system: str = "") -> list[dict]:
    return [{"role": "system", "content": SYSTEM.format(user=user, user_name=user_name, today=date.today().isoformat())
             + extra_system},
            {"role": "user", "content": question}]


def run(question: str, llm, emit: Callable[[dict], None], *, tool_list: list[toolcall.Tool] | None = None,
        host=None, approve: toolcall.Approver | None = None, user: str = "jaehwi", user_name: str = "이재휘",
        use_skills: bool = False, skill_list: list[skills_mod.Skill] | None = None,
        max_llm_calls: int = 8) -> toolcall.LoopResult:
    ts = tool_list if tool_list is not None else tools(host)
    extra, provider = "", ts
    if use_skills:
        sk = skill_list if skill_list is not None else skills_mod.load_all()
        session = skills_mod.SkillSession(ts, sk, base=[t.name for t in ts if t.source in BASE_SOURCES], emit=emit)
        extra, provider = skills_mod.skill_rules(sk), session.tools
        emit({"type": "skills", "node": "setup", "skills": skills_mod.to_json(sk)})
    first = provider() if callable(provider) else provider
    emit({"type": "tools", "node": "setup", "names": [t.name for t in first],
          "sources": {t.name: t.source for t in first}, "schemas": [t.schema() for t in first],
          "hidden": len(ts) - len([t for t in first if t.source != "skill"])})
    return toolcall.run_loop(llm, messages_for(question, user, user_name, extra), provider, emit=emit,
                             approve=approve, max_llm_calls=max_llm_calls)
