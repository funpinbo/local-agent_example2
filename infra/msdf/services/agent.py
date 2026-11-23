"""agent 서비스: 에이전트가 MSDF에 결과물을 요청하는 연결 지점.

MSDF의 order()는 결과를 기다리지 않고 바로 돌아오고, 결과는 delivery Action(on_result/on_failed)으로 따로 온다.
에이전트의 도구는 결과를 받아야 다음 판단을 하므로, fetch()가 order()를 내고 배달이 올 때까지 기다린다.

    from infra.msdf.services import agent
    agent.connect()      # MSDF_MODE=local이면 프로세스 안에 엔진, remote면 msdf up에 붙는다
    hits = agent.fetch("doc_search", {"corpus": "llm_server", "q": "OOM 원인", "k": 4})
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from ms_dataflow import action, order, service

service("agent")

MSDF_ROOT = Path(__file__).resolve().parents[1]

_cv = threading.Condition()
_done: dict[str, tuple[bool, Any]] = {}
_local_lock = threading.Lock()
_runner = None
_mode: str | None = None


class FetchError(RuntimeError):
    pass


@action(inputs=["result"])
def on_result(result, request_id: str):
    with _cv:
        _done[request_id] = (True, result)
        _cv.notify_all()


@action
def on_failed(status: str, error: str | None, request_id: str):
    with _cv:
        _done[request_id] = (False, f"{status}: {error}")
        _cv.notify_all()


def connect(mode: str | None = None, *, db: str | Path | None = None) -> str:
    """mode: local(기본, 환경 변수 MSDF_MODE) | remote."""
    global _runner, _mode
    mode = mode or os.getenv("MSDF_MODE", "local")
    if _mode == mode:
        return mode
    close()
    if mode == "local":
        from ms_dataflow.execute.local import LocalRunner
        db = db or MSDF_ROOT / ".msdf" / "local.db"
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        _runner = LocalRunner(MSDF_ROOT, db=db)
    elif mode == "remote":
        from ms_dataflow import start
        start(root=str(MSDF_ROOT), timeout=float(os.getenv("MSDF_CONNECT_TIMEOUT", "30")))
    else:
        raise ValueError(f"MSDF_MODE는 local 또는 remote: {mode}")
    _mode = mode
    return mode


def close() -> None:
    global _runner, _mode
    if _runner is not None:
        _runner.close()
    _runner, _mode = None, None


def runner():
    """local 모드의 LocalRunner (테스트에서 계획·캐시 확인용)."""
    return _runner


_listeners: list = []


def add_listener(fn) -> None:
    """fetch가 끝날 때마다 fn(info)를 부른다. info: order, params, request_id, mode, sec, steps, cached, ok."""
    _listeners.append(fn)


def remove_listener(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _short_params(params: dict) -> dict:
    return {k: (v if not isinstance(v, str) or len(v) <= 80 else v[:80] + "…") for k, v in params.items()}


def _remote_steps(rid: str) -> tuple[list[str], list[str]]:
    """엔진 API에서 run의 단계 기록을 읽는다. 실행한 단계와 캐시로 건너뛴 단계."""
    import httpx
    base = os.getenv("MSDF_SCHEDULER", "http://127.0.0.1:8787")
    try:
        r = httpx.get(f"{base}/api/runs/{rid}", timeout=5)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return [], []
    plan = data.get("run", {}).get("plan") or {}
    if isinstance(plan, str):
        import json
        try:
            plan = json.loads(plan)
        except ValueError:
            plan = {}
    ran = plan.get("steps") or [s.get("node") for s in data.get("steps", []) if s.get("node")]
    return ran, plan.get("cached", [])


def fetch(name: str, params: dict, timeout: float = 120.0) -> Any:
    """결과물 name을 params로 요청하고 결과를 돌려준다. 실패하면 FetchError."""
    import time
    if _mode is None:
        connect()
    t0 = time.perf_counter()
    steps: list[str] = []
    cached: list[str] = []
    if _mode == "local":
        # LangGraph가 도구를 병렬로 부를 수 있으므로 한 번에 한 요청만 엔진을 돌린다
        with _local_lock:
            rid = order(name, params=params)
            plan = _runner.last.plan if _runner.last else None
            if plan is not None:
                steps, cached = [s.node for s in plan.steps], list(plan.cached)
            _runner.run_until_idle()
    else:
        rid = order(name, params=params)
    with _cv:
        if not _cv.wait_for(lambda: rid in _done, timeout=timeout):
            raise FetchError(f"{name}: {timeout}초 안에 결과가 오지 않았습니다")
        ok, value = _done.pop(rid)
    if _mode == "remote" and _listeners:
        steps, cached = _remote_steps(rid)
    info = {"order": name, "params": _short_params(params), "request_id": rid, "mode": _mode,
            "sec": round(time.perf_counter() - t0, 2), "steps": steps, "cached": cached, "ok": ok}
    for fn in list(_listeners):
        try:
            fn(info)
        except Exception:
            pass
    if not ok:
        raise FetchError(f"{name}: {value}")
    return value
