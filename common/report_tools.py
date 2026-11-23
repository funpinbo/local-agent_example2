"""1단계 도구. 도구 설명이 LLM이 보는 전부라 설명을 구체적으로 쓴다."""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from common import docx_report
from common.templates.specs import SPECS


@tool
def list_template_fields(template: str) -> str:
    """보고서 양식의 칸 목록과 칸마다의 작성 기준을 알려 준다. template은 'weekly'(주간 업무 보고) 또는 'trip'(출장 결과 보고)."""
    try:
        return docx_report.describe_fields(template)
    except ValueError as e:
        return f"오류: {e}"


@tool
def save_report(template: str, fields: dict) -> str:
    """보고서를 docx로 저장한다. fields는 {칸 이름: 내용} 객체이며 양식의 모든 칸을 채워야 한다. 빠진 칸이 있으면 저장하지 않고 무엇이 빠졌는지 알려 준다."""
    try:
        path = docx_report.save(template, fields)
    except ValueError as e:
        return f"저장하지 않았습니다: {e}"
    return f"저장했습니다: {path.name}"


# --- 02 이후: 한 칸씩 채우는 도구 (State의 draft를 바꾼다) ---

@tool
def fill_field(field: str, value: str, runtime: ToolRuntime) -> Command:
    """보고서 초안의 칸 하나를 채운다. 이미 채운 칸을 다시 부르면 새 내용으로 덮어쓴다. field는 칸 이름(예: done), value는 그 칸에 들어갈 내용."""
    template = runtime.state.get("template")
    spec = SPECS.get(template or "")
    if spec is None:
        msg = "먼저 양식을 정해야 합니다."
    elif spec.field(field) is None:
        msg = f"'{field}'는 {spec.title} 양식에 없는 칸입니다. 가능한 칸: {', '.join(spec.keys())}"
    else:
        draft = {**(runtime.state.get("draft") or {}), field: value}
        left = [k for k in spec.keys() if not str(draft.get(k, "")).strip()]
        msg = f"'{field}' 칸을 채웠습니다. 남은 칸: {', '.join(left) if left else '없음'}"
        return Command(update={
            "draft": {field: value},
            "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id, name="fill_field")],
        })
    return Command(update={"messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id, name="fill_field")]})


@tool
def show_draft(runtime: ToolRuntime) -> str:
    """지금까지 채운 초안과 아직 비어 있는 칸을 보여 준다."""
    template = runtime.state.get("template")
    spec = SPECS.get(template or "")
    draft = runtime.state.get("draft") or {}
    if spec is None:
        return "양식이 정해지지 않았습니다."
    filled = {k: v for k, v in draft.items() if str(v).strip()}
    left = [k for k in spec.keys() if k not in filled]
    return json.dumps({"양식": spec.title, "채운 칸": filled, "남은 칸": left}, ensure_ascii=False)


@tool
def save_draft(runtime: ToolRuntime) -> Command:
    """채운 초안을 docx로 저장한다. 비어 있거나 너무 짧은 칸이 있으면 저장하지 않고 알려 준다."""
    template = runtime.state.get("template")
    draft = runtime.state.get("draft") or {}
    try:
        path: Path = docx_report.save(template, draft)
        msg, update = f"저장했습니다: {path.name}", {"saved_path": str(path)}
    except ValueError as e:
        msg, update = f"저장하지 않았습니다: {e}", {}
    return Command(update={**update,
                           "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id, name="save_draft")]})
