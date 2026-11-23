"""3·4단계 월간 보고서: 근거 id(mail:12, event:3, approval:5) 검사와 docx 저장."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt
from pydantic import BaseModel, Field

from common import config


class Section(BaseModel):
    heading: str = Field(description="섹션 제목")
    body: str = Field(description="본문. 문장마다 기록의 근거 id를 [mail:12], [event:3], [approval:5]처럼 단다")


class Report(BaseModel):
    title: str = Field(description="보고서 제목")
    sections: list[Section]

REF = re.compile(r"\[((?:mail|event|approval):\d+)\]")
REF_ANY = re.compile(r"((?:mail|event|approval):\d+)")      # 대괄호 없이 쓴 id도 찾는다
AUTHOR = "이재휘"


def refs(text: str) -> list[str]:
    return REF.findall(text or "")


def refs_loose(text: str) -> list[str]:
    return REF_ANY.findall(text or "")


def check_refs(body: str, known: set[str]) -> list[str]:
    cited = refs(body)
    problems = []
    if not cited:
        problems.append("근거 id([mail:12] 형식)가 하나도 없습니다.")
    unknown = sorted(set(cited) - known)
    if unknown:
        problems.append(f"기록에 없는 id를 인용했습니다: {', '.join(unknown)}")
    return problems


def save(title: str, sections: list[dict], *, summary: str = "", stem: str = "monthly",
         out_dir: Path | None = None) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "맑은 고딕"
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(title, level=1)
    doc.add_paragraph(f"작성자: {AUTHOR}    작성일: {date.today().isoformat()}")
    if summary:
        doc.add_heading("요약", level=2)
        doc.add_paragraph(summary)
    for s in sections:
        doc.add_heading(str(s.get("heading", "")), level=2)
        for para in str(s.get("body", "")).split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip())
    used = sorted({r for s in sections for r in refs(str(s.get("body", "")))})
    if used:
        doc.add_heading("근거", level=2)
        doc.add_paragraph(", ".join(used))
    out_dir = out_dir or config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}_{date.today():%Y%m%d}.docx"
    doc.save(path)
    return path
