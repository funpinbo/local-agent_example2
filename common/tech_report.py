"""2단계 기술 보고서: 근거 조각 정리, 인용 검사, docx 저장.

인용은 본문에 [chunk_id] 형식으로 단다. 예: "gpu-memory-utilization은 0.85부터 시작한다 [03_oom#2]."
인용 검사는 코드가 한다(틀리면 안 되는 일).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt
from pydantic import BaseModel, Field

from common import config

CITE = re.compile(r"\[([0-9A-Za-z_\-]+#\d+)\]")
AUTHOR = "이재휘"


class Section(BaseModel):
    heading: str = Field(description="섹션 제목")
    body: str = Field(description="본문. 문장마다 근거에 적힌 대괄호 id를 그대로 단다(형식: [파일이름#번호], 근거에 없는 id는 쓰지 않는다)")


def format_chunks(hits: list[dict]) -> str:
    """LLM에게 보여 줄 근거 묶음."""
    return "\n\n".join(f"[{h['chunk_id']}] ({h['source']} · {h.get('heading', '')})\n{h['text']}" for h in hits)


def citations(text: str) -> list[str]:
    return CITE.findall(text or "")


def check_citations(body: str, allowed: set[str]) -> list[str]:
    """문제 목록. 인용이 없거나, 주지 않은 조각을 인용했으면 문제."""
    cited = citations(body)
    problems = []
    if not cited:
        problems.append("근거 인용([chunk_id])이 하나도 없습니다.")
    unknown = sorted(set(cited) - allowed)
    if unknown:
        problems.append(f"주어진 근거에 없는 조각을 인용했습니다: {', '.join(unknown)}")
    return problems


def save_report(title: str, sections: list[dict | Section], *, summary: str = "",
                chunks: dict[str, dict] | None = None, stem: str = "tech_report",
                out_dir: Path | None = None) -> Path:
    """sections: [{heading, body}]. chunks(id → 조각)를 주면 끝에 참고 문서 목록을 단다."""
    secs = [s.model_dump() if isinstance(s, Section) else s for s in sections]
    doc = Document()
    doc.styles["Normal"].font.name = "맑은 고딕"
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(title, level=1)
    doc.add_paragraph(f"작성자: {AUTHOR}    작성일: {date.today().isoformat()}")
    if summary:
        doc.add_heading("요약", level=2)
        doc.add_paragraph(summary)
    for s in secs:
        doc.add_heading(s["heading"], level=2)
        for para in str(s["body"]).split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip())
    used = sorted({c for s in secs for c in citations(s["body"])} | set(citations(summary)))
    if used:
        doc.add_heading("참고 문서", level=2)
        for cid in used:
            c = (chunks or {}).get(cid)
            doc.add_paragraph(f"[{cid}] {c['source']} · {c.get('heading', '')}" if c else f"[{cid}] (확인되지 않은 조각)")
    out_dir = out_dir or config.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}_{date.today():%Y%m%d}.docx"
    doc.save(path)
    return path
