"""Skills (4편 5장). 특정 일을 하는 절차서를 필요할 때만 불러온다.

    chat_ui/skills/<이름>/SKILL.md
        ---
        name: notion-page-summary
        description: 한 줄 설명                 ← 평소 프롬프트에는 이것만 들어간다
        allowed-tools: [도구, ...]              ← 불러오면 도구를 이것만 남긴다
        ---
        절차 본문(마크다운)                     ← load_skill을 부르면 그때 대화에 들어간다

MCP는 "무엇에 닿을 수 있나(연결)", Skill은 "그걸로 어떻게 하나(절차)"를 맡는다.
2·3장에서 도구가 21개로 늘자 프롬프트가 7천 토큰을 넘고, 8B가 순서를 건너뛰었다(검색 없이 page_id를 지어냄).
Skill을 켜면 평소에는 한 줄 목록만 싣고, 불러온 뒤에는 그 일에 필요한 도구만 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from chat_ui.agent import toolcall

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


@dataclass
class Skill:
    name: str
    description: str
    allowed_tools: list[str]
    body: str
    path: Path


def load_all(root: Path = SKILLS_DIR) -> list[Skill]:
    out = []
    for f in sorted(root.glob("*/SKILL.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
        out.append(Skill(meta["name"], meta.get("description", ""), list(meta.get("allowed-tools") or []),
                         body.strip(), f))
    return out


def catalog_text(skills: list[Skill]) -> str:
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


@dataclass
class SkillSession:
    """한 번의 실행 동안 Skill 상태. tools()는 LLM을 부를 때마다 다시 읽힌다(run_loop가 매번 렌더링)."""

    all_tools: list[toolcall.Tool]
    skills: list[Skill]
    base: list[str] | None = None            # Skill을 불러오기 전에 붙여 둘 도구 이름(None이면 전부)
    active: Skill | None = None
    emit: object = None
    loaded: list[str] = field(default_factory=list)

    def __post_init__(self):
        names = [s.name for s in self.skills]
        self.loader = toolcall.Tool(
            "load_skill",
            "질문이 아래 Skill 설명에 맞으면 먼저 이 도구로 그 Skill의 절차를 불러온다. 불러온 절차를 그대로 따른다.",
            {"type": "object", "properties": {"name": {"type": "string", "enum": names}}, "required": ["name"]},
            self._load, source="skill")

    def _load(self, name: str) -> str:
        s = next((x for x in self.skills if x.name == name), None)
        if s is None:
            # 실제 실행에서 모델이 도구 이름(create_event, submit_approval)을 Skill로 불렀다.
            # "없다"만 돌려주면 모델이 포기하므로, 도구라면 바로 부르라고 알려 준다(2장: 모델이 고칠 수 있는 오류).
            tool_names = {t.name for t in self.tools()}
            if name in tool_names:
                return (f"오류: '{name}'은(는) Skill이 아니라 지금 바로 부를 수 있는 도구입니다. "
                        f"load_skill 없이 <function={name}>{{인자 JSON}}</function> 형식으로 바로 부르세요.")
            return (f"오류: '{name}' Skill은 없습니다. 있는 Skill: {', '.join(x.name for x in self.skills)}. "
                    f"Skill이 필요 없는 일이면 load_skill 없이 알맞은 도구를 바로 부르세요.")
        before = [t.name for t in self.tools()]
        self.active = s
        self.loaded.append(s.name)
        after = [t.name for t in self.tools()]
        if callable(self.emit):
            self.emit({"type": "skill", "node": "skill", "name": s.name, "allowed": s.allowed_tools,
                       "tools_before": len(before), "tools_after": len(after), "body_chars": len(s.body)})
        missing = [t for t in s.allowed_tools if t not in {x.name for x in self.all_tools}]
        note = f"\n\n(주의: 이 Skill이 쓰는 도구 중 지금 연결되지 않은 것: {', '.join(missing)})" if missing else ""
        return f"[Skill: {s.name}]\n{s.body}{note}\n\n지금부터 쓸 수 있는 도구: {', '.join(after)}"

    def tools(self) -> list[toolcall.Tool]:
        if self.active is not None:
            keep = set(self.active.allowed_tools)
            return [t for t in self.all_tools if t.name in keep]
        base = self.all_tools if self.base is None else [t for t in self.all_tools if t.name in set(self.base)]
        return [self.loader, *base]


def skill_rules(skills: list[Skill]) -> str:
    return ("\n\n[Skill]\n아래 목록의 일에만 절차서(Skill)가 있다. 질문이 아래 설명에 맞을 때만 먼저 load_skill로 불러 그 절차를 따른다. "
            "목록에 없는 일(메일 보내기, 일정 만들기, 결재 올리기 등)은 load_skill을 쓰지 말고 해당 도구를 바로 부른다. "
            "load_skill의 name에는 아래 Skill 이름만 쓴다.\n"
            + catalog_text(skills))


def to_json(skills: list[Skill]) -> list[dict]:
    return [{"name": s.name, "description": s.description, "allowed_tools": s.allowed_tools,
             "body_chars": len(s.body), "path": str(s.path.relative_to(SKILLS_DIR.parent.parent))} for s in skills]

