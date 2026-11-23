"""양식 칸 목록·검사·docx 저장. 틀리면 안 되는 일이라 코드가 한다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docxtpl import DocxTemplate
from pydantic import BaseModel, Field as PField, create_model

from common import config
from common.templates.specs import SPECS, TemplateSpec

AUTHOR = "이재휘"


def get_spec(template: str) -> TemplateSpec:
    if template not in SPECS:
        raise ValueError(f"없는 양식입니다: {template}. 가능한 양식: {', '.join(SPECS)}")
    return SPECS[template]


def describe_fields(template: str) -> str:
    spec = get_spec(template)
    lines = [f"[{spec.title}] 양식의 칸 {len(spec.fields)}개:"]
    lines += [f"- {f.key} ({f.label}): {f.guide}" for f in spec.fields]
    return "\n".join(lines)


def check(template: str, fields: dict) -> list[str]:
    """문제 목록. 비어 있으면 저장할 수 있다."""
    spec = get_spec(template)
    problems = []
    for f in spec.fields:
        value = str(fields.get(f.key, "") or "").strip()
        if not value:
            problems.append(f"'{f.key}'({f.label}) 칸이 비어 있습니다.")
        elif len(value) < f.min_len:
            problems.append(f"'{f.key}'({f.label}) 칸이 너무 짧습니다({len(value)}자).")
    extra = [k for k in fields if k not in spec.keys()]
    if extra:
        problems.append(f"양식에 없는 칸입니다: {', '.join(extra)}. 가능한 칸: {', '.join(spec.keys())}")
    return problems


def save(template: str, fields: dict, *, out_dir: Path | None = None, stem: str | None = None) -> Path:
    problems = check(template, fields)
    if problems:
        raise ValueError(" ".join(problems))
    out_dir = out_dir or config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl_path = config.TEMPLATE_DIR / f"{template}.docx"
    if not tpl_path.exists():
        from common.templates.make_templates import make
        make(template)
    doc = DocxTemplate(tpl_path)
    ctx = {k: str(v).strip() for k, v in fields.items()}
    ctx.update(author=AUTHOR, written_at=date.today().isoformat())
    doc.render(ctx)
    path = out_dir / f"{stem or template}_{date.today():%Y%m%d}.docx"
    doc.save(path)
    return path


def fields_model(template: str) -> type[BaseModel]:
    """구조화 출력용 스키마. 칸마다 str 하나."""
    spec = get_spec(template)
    defs = {f.key: (str, PField(description=f"{f.label}: {f.guide}")) for f in spec.fields}
    return create_model(f"{template.capitalize()}Fields", **defs)
