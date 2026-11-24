"""업무 비서. Tool Calling 층(toolcall.py) 위에 도구를 붙이고 루프 하나로 돈다.

- 1장: 도구는 3편에서 쓰던 함수(그룹웨어 읽기 10개 + 사내 문서 검색 1개)가 비서 코드 안에 있다(local).
- 2장: 여기에 MCP 서버가 준 도구가 더해진다(servers.yaml — time, filesystem, notion).
  모델이 보는 모양은 같다. 실행만 다른 프로세스로 JSON-RPC를 보내 한다.
- 3장: 그룹웨어 도구가 직접 만든 MCP 서버(groupware)로 나갔다. 그 서버가 붙어 있으면 비서 코드 안의
  그룹웨어 도구는 빼고(문서 검색만 남김), 쓰기 도구는 실행 전에 사람 승인을 받는다(toolcall.run_loop의 approve).
- 5장: Skill을 켜면 평소에는 Skill 한 줄 목록 + 기본 도구(비서 코드·그룹웨어·시간)만 싣고,
  load_skill로 절차를 불러오면 그 Skill의 allowed-tools만 남긴다(skills.py).
- 서비스 보강(블로그 이후): 대화 이어가기(history)와 단계 계획(planner).
  실제로 써 보니 "일정 만들고 그 내용으로 결재 올려 줘"에서 모델이 첫 단계만 하고 "진행하겠습니다"로 멈췄다.
  그래서 모델에게 먼저 단계 목록만 JSON으로 쓰게 하고, 단계가 둘 이상이면 코드가 한 단계씩 돌린다(3편 Planner–Executor).
  각 단계에서 모델은 그 단계의 도구 하나만 보고 인자만 채운다.
"""

from __future__ import annotations

import json
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
- 메일 보내기·일정 만들기·결재 올리기/결정은 사용자가 부탁했을 때만 부른다. 부탁받았으면 "진행할까요?"라고 말로 묻지 말고 해당 도구를 바로 부른다. 실행 전 사용자 승인은 시스템이 따로 받는다.
- 여러 단계를 부탁받으면(예: 일정을 만들고 그 내용으로 결재 올리기) 한 단계씩 도구를 부르고, 앞 단계 결과를 보고 다음 도구를 부른다. 모든 단계를 마친 뒤에 답한다.
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


def _history_messages(history: list[dict] | None) -> list[dict]:
    msgs = []
    for h in history or []:
        msgs.append({"role": "user", "content": h["user"]})
        done = ("\n(이 턴에 실행한 도구: " + "; ".join(h["actions"]) + ")") if h.get("actions") else ""
        msgs.append({"role": "assistant", "content": (h.get("assistant") or "") + done})
    return msgs


def messages_for(question: str, user: str = "jaehwi", user_name: str = "이재휘", extra_system: str = "",
                 history: list[dict] | None = None) -> list[dict]:
    """history: 같은 대화의 앞 턴들 [{"user": 질문, "assistant": 답, "actions": ["create_event … → event:17"]}]"""
    return ([{"role": "system", "content": SYSTEM.format(user=user, user_name=user_name,
                                                         today=date.today().isoformat()) + extra_system}]
            + _history_messages(history) + [{"role": "user", "content": question}])


WRITE_CLAIMS = ("올렸습니다", "보냈습니다", "추가했습니다", "등록했습니다", "만들었습니다", "상신했습니다",
                "제출했습니다", "승인했습니다", "반려했습니다", "올렸어요", "보냈어요")


def guard_write_claims(answer: str, events: list[dict], tools_: list[toolcall.Tool]) -> str:
    """쓰기를 했다고 답했는데 이번 실행에서 성공한 쓰기 도구가 하나도 없으면, 코드가 그 사실을 덧붙인다.
    (실제 실행에서 형식이 틀린 호출을 써 놓고 "결재를 올렸습니다"라고 답한 적이 있다.)"""
    writes = {t.name for t in tools_ if t.writes}
    did = any(e.get("type") == "tool" and e.get("name") in writes and e.get("ok") for e in events)
    if did or not any(c in answer for c in WRITE_CLAIMS):
        return answer
    return answer + "\n\n⚠ 확인: 이번 실행에서 실제로 실행된 쓰기 작업(메일·일정·결재)은 없습니다. 답의 내용과 다를 수 있습니다."


def action_summaries(events: list[dict], limit: int = 200) -> list[str]:
    """실행 이벤트에서 '도구 인자 → 결과'를 한 줄씩. 다음 턴이 앞 턴에서 무엇을 했는지 알 수 있게."""
    out = []
    for e in events:
        if e.get("type") == "tool" and e.get("name") != "load_skill":
            args = json.dumps(e.get("args"), ensure_ascii=False)
            out.append(f"{e['name']} {args[:limit]} → {str(e.get('content'))[:limit]}")
    return out


# ---------------------------------------------------------------- 단계 계획 (Planner–Executor)

PLANNER_SYSTEM = """너는 업무 비서의 계획 담당이다. 오늘은 {today}이고, 사용자는 {user_name}({user})이다.
사용자의 마지막 요청을 도구 실행 단계로 나눈다. 앞의 대화가 있으면 거기서 만든 일정·메일·결재 내용을 참고한다.
- 단계 하나는 도구 하나다. 아래 목록의 도구 이름만 쓴다.
- 앞 단계의 결과를 쓰는 단계는 goal에 무엇을 가져다 쓰는지 적는다.
- 사용자가 부탁하지 않은 쓰기(메일 보내기, 일정 만들기, 결재 올리기)는 넣지 않는다.
- 사용자가 쓰기를 부탁했으면 그 쓰기 도구(send_mail, create_event, submit_approval 등)가 반드시 단계에 들어가야 한다.
- 꼭 필요한 단계만 넣는다. 앞 단계 결과나 요청에서 알 수 없는 번호(id)가 있어야 하는 읽기 도구는 넣지 않는다.
- 도구가 필요 없는 요청이면 steps를 빈 목록으로 둔다.
[도구]
{tool_lines}
JSON만 쓴다: {{"steps": [{{"tool": "도구 이름", "goal": "이 단계에서 할 일"}}]}}"""

PLAN_SCHEMA = {"type": "object", "required": ["steps"], "properties": {"steps": {
    "type": "array", "items": {"type": "object", "required": ["tool", "goal"],
                               "properties": {"tool": {"type": "string"}, "goal": {"type": "string"}}}}}}

STEP_RULE = """

[지금 할 일]
여러 단계 중 {i}/{n}단계만 한다. 이 단계의 목표: {goal}
{previous}이 단계에서는 {tool} 도구 하나만 쓸 수 있다. 말로 설명하지 말고 바로 <function={tool}>{{인자 JSON}}</function>으로 부른다.
결과를 받으면 한 줄로 끝낸다."""


def plan(question: str, llm, tools_: list[toolcall.Tool], emit, *, user: str, user_name: str,
         history: list[dict] | None = None, max_steps: int = 5) -> list[dict]:
    """모델에게 단계 목록만 JSON으로 쓰게 한다(Ollama format으로 JSON 모양을 묶는다)."""
    lines = "\n".join(f"- {t.name}: {(t.description or '').splitlines()[0][:120] if t.description else ''}"
                      for t in tools_ if t.source != "skill")
    sysmsg = PLANNER_SYSTEM.format(today=date.today().isoformat(), user=user, user_name=user_name, tool_lines=lines)
    msgs = [{"role": "system", "content": sysmsg}] + _history_messages(history) + [{"role": "user", "content": question}]
    prompt = toolcall.render(msgs, [])
    out = llm.generate(prompt, format=PLAN_SCHEMA)
    emit({"type": "llm_raw", "node": "planner", "n": 0, "prompt": prompt, "prompt_delta": None, "output": out.text,
          "prompt_tokens": out.prompt_tokens, "output_tokens": out.output_tokens, "ms": out.ms,
          "done_reason": out.done_reason, "first_token": out.first_token, "request": out.request})
    names = {t.name for t in tools_}
    try:
        steps = json.loads(out.text).get("steps") or []
    except (json.JSONDecodeError, AttributeError):
        steps = []
    steps = [{"tool": st["tool"], "goal": str(st.get("goal", ""))} for st in steps
             if isinstance(st, dict) and st.get("tool") in names][:max_steps]
    emit({"type": "plan", "node": "planner", "plan_steps": steps})
    return steps


def execute_plan(question: str, steps: list[dict], llm, tools_: list[toolcall.Tool], emit, *,
                 approve, user: str, user_name: str, history: list[dict] | None = None) -> toolcall.LoopResult:
    """코드가 단계를 하나씩 돌린다. 모델은 그 단계의 도구 하나만 보고 인자를 채운다."""
    by_name = {t.name: t for t in tools_}
    done: list[dict] = []
    n_llm = n_tool = 0
    stopped = ""
    for i, st in enumerate(steps, 1):
        tool = by_name[st["tool"]]
        prev = "".join(f"- {d['i']}단계 {d['tool']} 결과: {d['result'][:600]}\n" for d in done)
        rule = STEP_RULE.format(i=i, n=len(steps), goal=st["goal"], tool=tool.name,
                                previous=("앞 단계 결과:\n" + prev) if prev else "")
        emit({"type": "step", "node": "executor", "i": i, "n": len(steps), "tool": tool.name, "goal": st["goal"]})
        result = None
        for attempt in range(2):          # 말로만 하고 안 부르면 한 번 더 시킨다
            msgs = messages_for(question, user, user_name, rule, history)
            if attempt:
                msgs.append({"role": "user", "content": f"설명하지 말고 지금 바로 {tool.name} 도구를 부르세요."})
            r = toolcall.run_loop(llm, msgs, [tool], emit=emit, approve=approve, max_llm_calls=3)
            n_llm += r.llm_calls
            n_tool += r.tool_calls
            tool_msgs = [m["content"] for m in r.messages if m.get("role") == "tool"]
            if tool_msgs:
                result = tool_msgs[-1]
                break
        if result is None:
            stopped = f"{i}단계({tool.name})에서 모델이 도구를 부르지 않았습니다."
            break
        failed = result.startswith("사용자가 실행을 거절했습니다") or result.startswith("오류")
        done.append({"i": i, "tool": tool.name, "result": result, "failed": failed})
        # 쓰기 단계가 실패·거절되면 멈춘다. 읽기 단계 실패(계획이 불필요한 조회를 넣은 경우 등)는 기록하고 넘어간다.
        if failed and tool.writes:
            stopped = f"{i}단계({tool.name})가 끝나지 않았습니다: {result[:200]}"
            break
    summary = "".join(f"- {d['i']}단계 {d['tool']}: {d['result'][:600]}\n" for d in done)
    final_rule = ("\n\n[실행 결과]\n" + (summary or "(실행한 단계 없음)\n")
                  + (f"멈춘 이유: {stopped}\n" if stopped else "")
                  + "위 단계는 이미 끝났다. 무엇을 했는지(못 했으면 왜인지) 사용자에게 2~3문장으로 답한다. "
                    "'~하겠습니다', '잠시만 기다려 주세요'처럼 앞으로 할 것처럼 쓰지 말고 '~했습니다'로 쓴다. "
                    "번호(id)는 위 결과에 있는 것만 그대로 쓴다. 도구는 부르지 않는다.")
    r = toolcall.run_loop(llm, messages_for(question, user, user_name, final_rule, history), [], emit=emit,
                          max_llm_calls=1)
    n_llm += r.llm_calls
    # 모델의 마무리 글은 번호를 틀리게 쓰기도 했다 → 실제로 실행한 것은 코드가 적어 덧붙인다
    answer = r.answer.strip() + "\n\n" + execution_record(done, stopped)
    return toolcall.LoopResult(answer, "plan_stopped" if stopped else "plan_done", r.messages, n_llm, n_tool)


def execution_record(done: list[dict], stopped: str) -> str:
    lines = ["[실행 기록]"]
    for d in done:
        mark = "✗" if d.get("failed") else "✓"
        try:
            res = json.loads(d["result"])
            shown = ", ".join(f"{k}: {v}" for k, v in res.items()) if isinstance(res, dict) and len(d["result"]) < 300 \
                else d["result"][:120]
        except (json.JSONDecodeError, TypeError):
            shown = d["result"][:120]
        lines.append(f"{mark} {d['i']}. {d['tool']} → {shown}")
    if stopped:
        lines.append(f"멈춤: {stopped}")
    return "\n".join(lines)


def run(question: str, llm, emit: Callable[[dict], None], *, tool_list: list[toolcall.Tool] | None = None,
        host=None, approve: toolcall.Approver | None = None, user: str = "jaehwi", user_name: str = "이재휘",
        use_skills: bool = False, skill_list: list[skills_mod.Skill] | None = None,
        use_planner: bool = False, history: list[dict] | None = None,
        max_llm_calls: int = 8) -> toolcall.LoopResult:
    ts = tool_list if tool_list is not None else tools(host)
    if use_planner:
        steps = plan(question, llm, ts, emit, user=user, user_name=user_name, history=history)
        by = {t.name: t for t in ts}
        # 단계가 둘 이상이거나 쓰기 단계가 있으면 코드가 돌린다. 쓰기 한 단계도 평소 루프에 맡겼더니
        # 실제 실행에서 "결재를 올리는 절차를 진행하겠습니다"라고 말만 하고 끝났다.
        # 읽기 한 단계짜리는 평소 루프로(필요하면 Skill도 쓴다).
        if len(steps) >= 2 or any(by[st["tool"]].writes for st in steps):
            emit({"type": "tools", "node": "setup", "names": [t.name for t in ts],
                  "sources": {t.name: t.source for t in ts}, "schemas": [t.schema() for t in ts], "hidden": 0})
            return execute_plan(question, steps, llm, ts, emit, approve=approve, user=user, user_name=user_name,
                                history=history)
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
    return toolcall.run_loop(llm, messages_for(question, user, user_name, extra, history), provider, emit=emit,
                             approve=approve, max_llm_calls=max_llm_calls)
