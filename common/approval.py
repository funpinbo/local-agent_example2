"""결재 상신. 사람이 승인한 뒤에만 부른다(4단계 04·05)."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from common import config, gw_tools

LINE = ["jiyoung", "dongho"]     # 팀장 → 본부장


def submit(draft: dict, docx_path: str) -> dict:
    blob = base64.b64encode(Path(docx_path).read_bytes()).decode()
    r = httpx.post(f"{config.GROUPWARE_URL}/api/approvals", headers={"X-User": gw_tools.USER}, timeout=10, json={
        "kind": draft["kind"], "title": draft["title"], "body": draft["body"], "line": draft["line"],
        "attachment_b64": blob, "attachment_name": draft["attachment"]})
    r.raise_for_status()
    return r.json()
