"""MCP 호스트 (4편 2장). 비서가 여러 MCP 서버에 붙어 도구를 받아 오고, 부르고, 오간 JSON-RPC를 전부 남긴다.

1장에서는 도구가 비서 코드 안의 함수였다. 여기서는 도구가 다른 프로세스(MCP 서버)에 있고,
비서는 "도구 목록을 달라(tools/list)", "이 도구를 이 인자로 불러 달라(tools/call)"를 JSON-RPC로 보낸다.
모델이 보는 것은 1장과 같다 — 서버가 준 inputSchema가 그대로 chat template에 들어간다.

- 서버마다 연결을 하나씩 길게 열어 둔다(stdio면 자식 프로세스가 계속 떠 있다).
  MCP 세션은 연 작업(task)에서 닫아야 해서, 서버마다 작업 하나를 두고 요청은 큐로 넘긴다.
- 전송 계층을 한 겹 감싸(Tap) 보내고 받은 메시지를 그대로 이벤트로 내보낸다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from chat_ui.agent import toolcall
from common import config

CONFIG = Path(__file__).with_name("servers.yaml")
USER_META_KEY = "local.office/user"          # 그룹웨어 MCP 서버가 읽는 "누구 대신" 키
_VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


# ---------------------------------------------------------------- 설정

def load_env_file(path: Path = config.ROOT / ".env") -> dict[str, str]:
    """프로젝트 루트 .env (KEY=VALUE 줄). 토큰 같은 비밀은 여기에 두고 git에 올리지 않는다."""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _expand(v: Any, env: dict[str, str], missing: set[str]) -> Any:
    if isinstance(v, str):
        def sub(m: re.Match) -> str:
            if m.group(1) not in env or not env[m.group(1)]:
                missing.add(m.group(1))
                return ""
            return env[m.group(1)]
        return _VAR.sub(sub, v)
    if isinstance(v, list):
        return [_expand(x, env, missing) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x, env, missing) for k, x in v.items()}
    return v


def _command(cmd: str) -> str:
    """Windows에서 npx는 셸 스크립트라 직접 실행이 안 된다 → npx.cmd를 찾는다."""
    if os.name == "nt" and not cmd.lower().endswith((".exe", ".cmd", ".bat")):
        found = shutil.which(cmd + ".cmd")
        if found:
            return found
    return cmd


# ---------------------------------------------------------------- 메시지 엿보기

class _TapRead:
    def __init__(self, inner, on_msg):
        self.inner, self.on_msg = inner, on_msg

    async def receive(self):
        item = await self.inner.receive()
        self.on_msg("recv", item)
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except Exception as e:  # 스트림이 닫히면 반복을 끝낸다
            if type(e).__name__ in ("EndOfStream", "ClosedResourceError", "BrokenResourceError"):
                raise StopAsyncIteration from e
            raise

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *a):
        return await self.inner.__aexit__(*a)

    def __getattr__(self, name):          # last_context 같은 추가 속성
        return getattr(self.inner, name)


class _TapWrite:
    def __init__(self, inner, on_msg):
        self.inner, self.on_msg = inner, on_msg

    async def send(self, item, /):
        self.on_msg("send", item)
        await self.inner.send(item)

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *a):
        return await self.inner.__aexit__(*a)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class Tap:
    """전송(stdio / Streamable HTTP)을 감싸 오가는 JSON-RPC 메시지를 on_msg로 넘긴다."""

    def __init__(self, inner_cm, on_msg: Callable[[str, Any], None]):
        self.inner_cm, self.on_msg = inner_cm, on_msg

    async def __aenter__(self):
        read, write = await self.inner_cm.__aenter__()
        return _TapRead(read, self.on_msg), _TapWrite(write, self.on_msg)

    async def __aexit__(self, *a):
        return await self.inner_cm.__aexit__(*a)


def message_dict(item: Any) -> dict:
    msg = getattr(item, "message", item)
    if isinstance(item, Exception):
        return {"error": f"{type(item).__name__}: {item}"}
    if hasattr(msg, "model_dump"):
        return msg.model_dump(by_alias=True, exclude_none=True, mode="json")
    return {"raw": str(msg)}


# ---------------------------------------------------------------- 서버 하나

@dataclass
class Server:
    name: str
    cfg: dict
    status: str = "off"                    # off | needs_env | connecting | ready | error
    error: str = ""
    missing_env: list[str] = field(default_factory=list)
    protocol: str = ""
    info: dict = field(default_factory=dict)
    offered: list[dict] = field(default_factory=list)       # 서버가 준 도구 전부 (이름, 설명, 읽기 전용 여부)
    used: list[toolcall.Tool] = field(default_factory=list)  # 비서에 붙인 도구
    queue: asyncio.Queue | None = None
    ready: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict:
        return {"name": self.name, "status": self.status, "error": self.error, "missing_env": self.missing_env,
                "transport": self.cfg.get("transport"), "protocol": self.protocol, "info": self.info,
                "offered": self.offered, "used": [t.name for t in self.used]}


class McpHost:
    def __init__(self, config_path: Path = CONFIG, env: dict[str, str] | None = None, connect_timeout: float = 180):
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        base_env = {**load_env_file(), **os.environ, "PYTHON": sys.executable, "ROOT": str(config.ROOT),
                    **(env or {})}
        self.servers: dict[str, Server] = {}
        for name, cfg in (raw.get("servers") or {}).items():
            missing: set[str] = set()
            s = Server(name, _expand(cfg, base_env, missing))
            if missing:
                s.status, s.missing_env = "needs_env", sorted(missing)
            self.servers[name] = s
        self.connect_timeout = connect_timeout
        self._listeners: list[Callable[[dict], None]] = []
        self.boot_log: list[dict] = []          # 실행 밖(연결할 때)에 오간 메시지
        self.acting_user: str | None = None     # 지금 실행의 사용자. pass_user 서버에 _meta로 실어 보낸다
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-host")
        self._thread.start()
        self._lock = threading.Lock()

    # ---- 메시지 → 이벤트
    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _on_msg(self, server: str, direction: str, item: Any) -> None:
        msg = message_dict(item)
        ev = {"type": "mcp", "node": f"mcp:{server}", "server": server, "dir": direction,
              "method": msg.get("method"), "rpc_id": msg.get("id"), "msg": msg,
              "bytes": len(json.dumps(msg, ensure_ascii=False))}
        if not self._listeners:
            self.boot_log.append(ev)
            del self.boot_log[:-200]
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 — 기록 실패가 호출을 막지 않게
                pass

    # ---- 연결
    def _transport(self, s: Server):
        cfg = s.cfg
        tap = lambda d, item: self._on_msg(s.name, d, item)  # noqa: E731
        if cfg.get("transport") == "http":
            from mcp.shared._httpx_utils import create_mcp_http_client   # 헤더(서비스 키)는 HTTP 클라이언트에 싣는다
            http = create_mcp_http_client(headers=cfg.get("headers") or None)
            return Tap(streamable_http_client(cfg["url"], http_client=http), tap)
        params = StdioServerParameters(command=_command(cfg["command"]), args=cfg.get("args", []),
                                       env=cfg.get("env") or None, cwd=cfg.get("cwd") or str(config.ROOT))
        return Tap(stdio_client(params, errlog=open(os.devnull, "w")), tap)

    async def _serve(self, s: Server) -> None:
        s.queue = asyncio.Queue()
        try:
            async with Client(self._transport(s), read_timeout_seconds=120) as c:
                s.protocol = c.protocol_version
                info = c.server_info
                s.info = {"name": info.name, "version": info.version} if info else {}
                listed = await c.list_tools()
                s.offered, s.used = self._select(s, listed.tools)
                s.status = "ready"
                s.ready.set()
                while True:
                    job = await s.queue.get()
                    if job is None:
                        break
                    fn, fut = job
                    try:
                        fut.set_result(await fn(c))
                    except Exception as e:  # noqa: BLE001
                        fut.set_exception(e)
        except Exception as e:  # noqa: BLE001
            s.status, s.error = "error", f"{type(e).__name__}: {e}"
        finally:
            if s.status == "ready":
                s.status = "off"
            s.ready.set()

    def _select(self, s: Server, tools) -> tuple[list[dict], list[toolcall.Tool]]:
        allow = s.cfg.get("tools")
        verified = set(s.cfg.get("verified_read") or [])   # 서버 표시와 달리 우리가 읽기라고 확인한 도구
        offered, used = [], []
        for t in tools:
            hint = bool(t.annotations and t.annotations.read_only_hint)
            ro = hint or t.name in verified
            keep = (ro or not s.cfg.get("read_only")) and (not allow or t.name in allow)
            offered.append({"name": t.name, "description": (t.description or "")[:200], "read_only": ro,
                            "read_only_hint": hint, "used": keep})
            if keep:
                used.append(toolcall.Tool(t.name, t.description or "", t.input_schema or {"type": "object", "properties": {}},
                                          self._caller(s.name, t.name), writes=not ro, source=f"mcp:{s.name}"))
        return offered, used

    def connect(self, names: list[str] | None = None) -> list[dict]:
        """아직 안 붙은 서버에 붙는다(필요한 환경 변수가 없는 서버는 건너뛴다). 상태 목록을 돌려준다."""
        with self._lock:
            todo = [s for n, s in self.servers.items() if (names is None or n in names) and s.status in ("off", "error")]
            for s in todo:
                s.status, s.error = "connecting", ""
                s.ready.clear()
                asyncio.run_coroutine_threadsafe(self._serve(s), self._loop)
            for s in todo:
                if not s.ready.wait(self.connect_timeout):
                    s.status, s.error = "error", f"{self.connect_timeout:.0f}초 안에 연결되지 않았습니다"
        return self.status()

    def status(self) -> list[dict]:
        return [s.view() for s in self.servers.values()]

    def tools(self) -> list[toolcall.Tool]:
        return [t for s in self.servers.values() if s.status == "ready" for t in s.used]

    # ---- 호출
    def _submit(self, server: str, fn, timeout: float = 120):
        s = self.servers[server]
        if s.status != "ready" or s.queue is None:
            raise RuntimeError(f"MCP 서버 '{server}'에 연결되어 있지 않습니다({s.status})")

        async def go():
            fut = self._loop.create_future()
            await s.queue.put((fn, fut))
            return await fut
        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)

    def _caller(self, server: str, tool: str):
        def call(**args) -> str:
            meta = {USER_META_KEY: self.acting_user} if self.servers[server].cfg.get("pass_user") and self.acting_user else None
            r = self._submit(server, lambda c: c.call_tool(tool, args, meta=meta))
            texts = [getattr(b, "text", None) or json.dumps(b.model_dump(mode="json"), ensure_ascii=False)
                     for b in (r.content or [])]
            out = "\n".join(texts) if texts else json.dumps(r.structured_content or {}, ensure_ascii=False)
            return f"오류(서버가 isError로 돌려줌): {out}" if r.is_error else out
        return call

    def close(self) -> None:
        for s in self.servers.values():
            if s.queue is not None and s.status == "ready":
                self._loop.call_soon_threadsafe(s.queue.put_nowait, None)
        deadline = time.time() + 10
        while time.time() < deadline and any(s.status == "ready" for s in self.servers.values()):
            time.sleep(0.05)
        self._loop.call_soon_threadsafe(self._loop.stop)


_default: McpHost | None = None
_default_lock = threading.Lock()


def default() -> McpHost:
    global _default
    with _default_lock:
        if _default is None:
            _default = McpHost()
        return _default


def set_default(h: McpHost | None) -> None:
    global _default
    _default = h
