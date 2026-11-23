r"""업무 비서 서비스 (4편). 그룹웨어와 따로 도는 서버다.

    .\.venv\Scripts\python.exe -m uvicorn chat_ui.server:app --port 8200
    그룹웨어(8100) 화면 오른쪽 비서 패널이 이 서버를 부른다. 3편 챗봇 화면은 http://127.0.0.1:8200/chat

- 실행 진행은 두 가지로 받을 수 있다: 폴링(GET /chat/api/run/{id}?since=n, 3편 방식)과
  SSE(GET /chat/api/run/{id}/events, 서버가 이벤트가 생길 때마다 밀어 준다).
- 그룹웨어 데이터는 이 서버가 직접 읽지 않는다. 3편 도구는 MSDF → 그룹웨어 REST를 거친다.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from chat_ui import runner, uploads
from common import config

STATIC = Path(__file__).resolve().parent / "static"


class RunIn(BaseModel):
    key: str
    text: str = ""
    template: str | None = None
    month: str | None = None
    parallel: str | None = None
    shared: str | None = None
    cyclic: str | None = None
    skills: str | None = None           # 업무 비서: on | off (stage보다 우선)
    stage: str | None = None            # 업무 비서: 1 | 2 | 3 | 5 — 장별 도구 구성
    memo_source: str | None = None
    sample_memo: str | None = None
    use_uploads: bool = True
    user: str | None = None             # 업무 비서: 그룹웨어 화면의 지금 사용자


class ResumeIn(BaseModel):
    action: str                 # approve | reject | edit
    title: str | None = None
    args: dict | None = None    # 업무 비서: 고친 도구 인자
    comment: str = ""


def router() -> APIRouter:
    r = APIRouter(prefix="/chat")

    @r.get("", response_class=HTMLResponse)
    @r.get("/", response_class=HTMLResponse)
    def page():
        return HTMLResponse((STATIC / "chat.html").read_text(encoding="utf-8"))

    @r.get("/api/catalog")
    def catalog():
        return {"stages": runner.catalog(), "model": config.LLM_MODEL,
                "msdf_mode": os.getenv("MSDF_MODE", "local"),
                "msdf_ui": os.getenv("MSDF_SCHEDULER", "http://127.0.0.1:8787").rstrip("/") + "/ui/"}

    @r.post("/api/run")
    def run(body: RunIn):
        if body.key not in runner.BY_KEY:
            raise HTTPException(404, "없는 실습입니다")
        opts = {k: v for k, v in body.model_dump().items() if v not in (None, "")}
        return runner.start(body.key, opts).view()

    @r.get("/api/run/{job_id}")
    def poll(job_id: str, since: int = 0):
        job = runner.JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "없는 작업입니다")
        return job.view(since)

    @r.get("/api/run/{job_id}/events")
    async def events(job_id: str, request: Request, since: int = 0,
                     last_event_id: str | None = Header(default=None)):
        """SSE. 이벤트 하나 = `event: step`, 상태가 바뀌면 `event: status`. 끝나면(done/error) 스트림을 닫는다.
        연결이 끊겼다 다시 붙으면 브라우저가 Last-Event-ID를 보내고, 그 다음 이벤트부터 이어 준다."""
        job = runner.JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "없는 작업입니다")
        start = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else since

        def sse(event: str, data, eid: int | None = None) -> str:
            head = f"id: {eid}\n" if eid is not None else ""
            return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        async def gen():
            n, sent_status = start, None
            yield "retry: 2000\n\n"
            while True:
                status = job.status
                new = job.events[n:]
                for i, ev in enumerate(new):
                    yield sse("step", ev, n + i)
                n += len(new)
                if status != sent_status:
                    yield sse("status", job.status_view())
                    sent_status = status
                if status in ("done", "error") and n >= len(job.events):
                    return
                if await request.is_disconnected():
                    return
                if not await asyncio.to_thread(job.wait_change, n, status, 15):
                    yield ": keep-alive\n\n"          # 15초 동안 아무 일이 없으면 연결 유지용 주석

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.post("/api/run/{job_id}/resume")
    def resume(job_id: str, body: ResumeIn):
        if job_id not in runner.JOBS:
            raise HTTPException(404, "없는 작업입니다")
        ans: dict = {"action": body.action}
        if body.action == "edit" and body.title:
            ans["changes"] = {"title": body.title}
        if body.args is not None:
            ans["args"] = body.args
        if body.comment:
            ans["comment"] = body.comment
        try:
            return runner.resume(job_id, ans).view()
        except ValueError as e:
            raise HTTPException(409, str(e))

    @r.get("/api/mcp")
    def mcp_status():
        """MCP 서버 상태(연결 전이면 off/needs_env)와 연결할 때 오간 메시지."""
        host = runner.get_mcp_host()
        if host is None:
            return {"servers": [], "boot_log": [], "disabled": True}
        return {"servers": host.status(), "boot_log": host.boot_log[-60:], "disabled": False}

    @r.post("/api/mcp/connect")
    def mcp_connect():
        host = runner.get_mcp_host()
        if host is None:
            raise HTTPException(409, "MCP가 꺼져 있습니다")
        return {"servers": host.connect()}

    @r.get("/api/uploads")
    def list_uploads():
        return {"files": uploads.list_files(), "corpus": uploads.corpus()}

    @r.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(413, "5MB까지 올릴 수 있습니다")
        try:
            info = uploads.add(file.filename or "doc.txt", data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:   # MSDF가 요청을 받지 못한 경우(엔진이 새 DAG를 모름, 서비스 미연결 등)
            raise HTTPException(503, f"MSDF 요청 실패({type(e).__name__}): {e} — "
                                     "msdf up과 docs 서비스를 최신 코드로 다시 띄웠는지 확인하세요.")
        return {**info, "files": uploads.list_files()}

    @r.delete("/api/uploads")
    def clear(name: str | None = None):
        return {"corpus": uploads.remove(name), "files": uploads.list_files()}

    @r.get("/files/{name}")
    def download(name: str):
        path = (config.OUTPUT_DIR / Path(name).name).resolve()
        if path.parent != config.OUTPUT_DIR.resolve() or not path.exists():
            raise HTTPException(404, "파일이 없습니다")
        return FileResponse(path, filename=path.name)

    return r


def create(*, connect_msdf: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if connect_msdf:
            from infra.msdf.services import agent
            agent.connect()          # MSDF_MODE(local|remote)를 따른다
        yield
        from chat_ui.agent import mcp_host
        if mcp_host._default is not None:   # 띄운 MCP 서버(자식 프로세스)를 닫는다
            mcp_host._default.close()

    app = FastAPI(title="업무 비서 (실습용)", lifespan=lifespan)
    # 그룹웨어 화면(8100)과 개발 서버(5173)가 다른 출처에서 부른다
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(router())

    @app.get("/", include_in_schema=False)
    def root():
        return HTMLResponse("<meta charset=utf-8><p>업무 비서 서버입니다. 그룹웨어 화면의 비서 패널에서 씁니다. "
                            "3편 챗봇: <a href='/chat'>/chat</a></p>")
    return app


def __getattr__(name: str):
    if name == "app":
        globals()["app"] = create()
        return globals()["app"]
    raise AttributeError(name)
