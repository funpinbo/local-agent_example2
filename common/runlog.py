"""실행 로그. 화면에 찍고 blog/logs/<이름>_<시각>.jsonl에 남긴다.

글에 넣는 한계·실패 로그는 이 파일에서만 가져온다(지어내지 않는다).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.types import Command

from common import config


def _short(text: Any, n: int = 300) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def describe(msg: BaseMessage) -> dict:
    d: dict[str, Any] = {"type": msg.type, "content": msg.content}
    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            d["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in msg.tool_calls]
        if msg.invalid_tool_calls:
            d["invalid_tool_calls"] = [c.get("error") for c in msg.invalid_tool_calls]
        raw = msg.response_metadata.get("raw_content")
        if raw is not None:
            d["raw_content"] = raw
        if msg.usage_metadata:
            d["tokens"] = {"in": msg.usage_metadata.get("input_tokens"),
                           "out": msg.usage_metadata.get("output_tokens")}
    if isinstance(msg, ToolMessage):
        d["tool"] = msg.name
    return d


class RunLog:
    def __init__(self, name: str, *, enabled: bool = True, meta: dict | None = None):
        self.name = name
        self.enabled = enabled
        self.t0 = time.perf_counter()
        self.path: Path | None = None
        if enabled:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path = config.LOG_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
            self.write("start", {"model": config.LLM_MODEL, **(meta or {})})

    def write(self, kind: str, data: Any) -> None:
        if not self.enabled:
            return
        rec = {"t": round(time.perf_counter() - self.t0, 2), "kind": kind, "data": data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        try:
            print(f"[{rec['t']:7.2f}s] {kind:14s} {_short(data)}", flush=True)
        except UnicodeEncodeError:
            sys.stdout.buffer.write((f"[{rec['t']:7.2f}s] {kind} {_short(data)}\n").encode("utf-8"))

    def message(self, node: str, msg: BaseMessage) -> None:
        self.write(f"{node}:{msg.type}", describe(msg))

    def stream(self, graph, inputs: Any, config_: dict | None = None) -> dict:
        """graph.stream(updates)를 돌며 노드마다 새 메시지와 State 변화를 기록하고, 마지막 State를 돌려준다."""
        cfg = {"recursion_limit": 60, **(config_ or {})}
        state: dict = {}
        from infra.msdf.services import agent as msdf     # 실행 중 나간 MSDF 요청도 같이 남긴다

        def on_fetch(info: dict) -> None:
            self.write("msdf", {k: info[k] for k in ("order", "params", "mode", "sec", "steps", "cached", "ok")})
        msdf.add_listener(on_fetch)
        try:
            self._stream(graph, inputs, cfg, state)
        finally:
            msdf.remove_listener(on_fetch)
        return self._last

    def _stream(self, graph, inputs: Any, cfg: dict, state: dict) -> None:
        self._last = state
        for mode, step in graph.stream(inputs, cfg, stream_mode=["updates", "values"]):
            if mode == "values":
                self._last = step
                continue
            for node, update in step.items():
                if isinstance(update, (list, tuple)) and update:
                    # 도구 여러 개를 한 번에 부르면 ToolNode가 Command/dict 목록을 돌려준다
                    merged: dict = {"messages": []}
                    for item in update:
                        u = (item.update if isinstance(item, Command) else item) or {}
                        if not isinstance(u, dict):
                            continue
                        merged["messages"] += u.get("messages", [])
                        for k, v in u.items():
                            if k != "messages":
                                merged.setdefault(k, []).append(v)
                    update = merged
                if not isinstance(update, dict):
                    self.write(node, update)
                    continue
                for msg in update.get("messages", []) or []:
                    if isinstance(msg, BaseMessage):
                        self.message(node, msg)
                rest = {k: v for k, v in update.items() if k != "messages"}
                if rest:
                    self.write(f"{node}:state", rest)
