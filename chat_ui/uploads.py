"""챗봇에서 올린 문서. 사내 문서 검색(MSDF doc_search)이 같이 찾도록 문서 묶음(corpus) 하나로 저장한다.

올린 파일 묶음이 바뀔 때마다 내용 해시로 새 묶음 이름(uploads/<해시>)을 만든다.
MSDF는 묶음 이름이 다르면 처음부터 읽고 청킹·색인하므로, 캐시 때문에 새 파일이 안 보이는 일이 없다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from io import BytesIO
from pathlib import Path

from docx import Document

from common import config

DOCS_ROOT = config.SAMPLE_DIR / "docs"
STORE = DOCS_ROOT / "_uploaded_files"          # 원본(정리한 텍스트) 보관
STATE = STORE / "_state.json"
ALLOWED = {".md", ".txt", ".docx"}


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z가-힣_\-]+", "_", Path(name).stem).strip("_") or "doc"
    return stem[:60]


def _to_markdown(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext == ".docx":
        doc = Document(BytesIO(data))
        lines = []
        for p in doc.paragraphs:
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name or "").lower()
            lines.append(f"## {t}" if style.startswith("heading") else t)
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n".join(lines)
    else:
        text = data.decode("utf-8", errors="replace")
    if not text.lstrip().startswith("# "):
        text = f"# {Path(name).stem}\n\n{text}"
    return text


def list_files() -> list[dict]:
    if not STORE.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size}
            for p in sorted(STORE.glob("*.md"))]


def corpus() -> str | None:
    """지금 올라온 파일들로 만든 검색용 묶음 이름. 파일이 없으면 None."""
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8")).get("corpus")
    return None


BASE_CORPUS = "llm_server"


def _rebuild() -> str | None:
    """사내 문서(llm_server) + 올린 문서를 합친 묶음을 만든다. 올린 문서가 없으면 None."""
    files = sorted(STORE.glob("*.md")) if STORE.exists() else []
    old = DOCS_ROOT / "uploads"
    if not files:
        if STATE.exists():
            STATE.unlink()
        shutil.rmtree(old, ignore_errors=True)
        return None
    base = sorted((DOCS_ROOT / BASE_CORPUS).glob("*.md"))
    h = hashlib.sha1(b"".join(p.name.encode() + p.read_bytes() for p in base + files)).hexdigest()[:10]
    name = f"uploads/{h}"
    target = DOCS_ROOT / name
    if not target.exists():
        shutil.rmtree(old, ignore_errors=True)
        target.mkdir(parents=True)
        for p in base + files:
            shutil.copy(p, target / p.name)
    STATE.write_text(json.dumps({"corpus": name}), encoding="utf-8")
    return name


def add(name: str, data: bytes) -> dict:
    """형식 변환(docx → 마크다운)만 여기서 하고, 저장은 MSDF의 doc_ingest(docs 서비스)가 한다."""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"올릴 수 있는 형식은 {', '.join(sorted(ALLOWED))}입니다.")
    text = _to_markdown(name, data)
    from infra.msdf.services import agent
    stored = agent.fetch("doc_ingest", {"name": f"up_{_safe_name(name)}.md", "text": text})
    return {**stored, "corpus": _rebuild()}


def remove(name: str | None = None) -> str | None:
    if name:
        (STORE / Path(name).name).unlink(missing_ok=True)
    elif STORE.exists():
        for p in STORE.glob("*.md"):
            p.unlink()
    return _rebuild()


def latest_text() -> str | None:
    files = sorted(STORE.glob("*.md"), key=lambda p: p.stat().st_mtime) if STORE.exists() else []
    return files[-1].read_text(encoding="utf-8") if files else None
