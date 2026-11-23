"""docx 양식 만들기 (docxtpl의 {{ 칸 }} 자리표시자).  python -m common.templates.make_templates"""

from __future__ import annotations

from docx import Document
from docx.shared import Pt

from common import config
from common.templates.specs import SPECS


def make(name: str) -> str:
    spec = SPECS[name]
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "맑은 고딕"
    style.font.size = Pt(10.5)
    doc.add_heading(spec.title, level=1)
    doc.add_paragraph("작성자: {{ author }}    작성일: {{ written_at }}")
    table = doc.add_table(rows=len(spec.fields), cols=2)
    table.style = "Table Grid"
    for row, field in zip(table.rows, spec.fields):
        row.cells[0].text = field.label
        row.cells[1].text = "{{ " + field.key + " }}"
    path = config.TEMPLATE_DIR / f"{name}.docx"
    doc.save(path)
    return str(path)


if __name__ == "__main__":
    for n in SPECS:
        print("saved", make(n))
