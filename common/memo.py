"""1장 메모 읽기. 예시 메모(common/sample_data/*.txt)는 MSDF의 memo_text로 받는다.

다른 위치의 파일을 직접 넘기면(사용자가 가져온 메모) 그 파일을 그대로 읽는다.
"""

from __future__ import annotations

from pathlib import Path

from common import config


def load(path: str | Path) -> str:
    p = Path(path)
    if p.suffix == ".txt" and p.resolve().parent == config.SAMPLE_DIR.resolve():
        from infra.msdf.services import agent
        return agent.fetch("memo_text", {"name": p.name})
    return p.read_text(encoding="utf-8")
