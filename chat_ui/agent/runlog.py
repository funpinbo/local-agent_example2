"""비서 실행 기록을 PostgreSQL agent 스키마에 남긴다 (runs / llm_calls / tool_calls).

기록은 곁가지다. DB에 못 붙으면 경고 이벤트 한 번만 남기고 비서는 그대로 돈다.
접속은 assistant 계정 — 그룹웨어 스키마는 읽을 수 없다(infra/db/init/00_roles.sql).
"""

from __future__ import annotations

import logging
import os
import threading

from psycopg.types.json import Jsonb

from infra.groupware import db

DSN = os.getenv("AGENT_DSN", "postgresql://assistant:assistant@127.0.0.1:15432/office")
SCHEMA = os.getenv("AGENT_SCHEMA", "agent")
MIGRATIONS = db.MIGRATIONS.parent / "agent"

log = logging.getLogger(__name__)


class RunLog:
    def __init__(self, dsn: str = DSN, schema: str = SCHEMA):
        self.dsn, self.schema = dsn, schema
        self._store: db.Store | None = None
        self._lock = threading.Lock()
        self.error = ""

    def _get(self) -> db.Store | None:
        with self._lock:
            if self._store is None and not self.error:
                try:
                    db.migrate(self.dsn, self.schema, migrations=MIGRATIONS)
                    self._store = db.Store(self.dsn, self.schema, max_size=4)
                except Exception as e:  # noqa: BLE001
                    self.error = f"실행 기록 DB에 붙지 못했습니다({type(e).__name__}: {e})"
                    log.warning(self.error)
            return self._store

    def _exec(self, sql: str, args) -> bool:
        s = self._get()
        if s is None:
            return False
        try:
            with s.tx() as c:
                c.execute(sql, args)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("실행 기록 실패: %s", e)
            return False

    def start(self, run_id: str, kind: str, user: str | None, text: str, model: str | None) -> bool:
        return self._exec("INSERT INTO runs(id,kind,user_id,input,model) VALUES(%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                          (run_id, kind, user, text, model))

    def event(self, run_id: str, ev: dict) -> None:
        """Tool Calling 층이 내는 이벤트(llm_raw, tool)를 행으로."""
        if ev.get("type") == "llm_raw":
            self._exec("INSERT INTO llm_calls(run_id,seq,prompt,output,prompt_tokens,output_tokens,ms,done_reason,first_token,request)"
                       " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                       (run_id, ev["n"], ev["prompt"], ev["output"], ev.get("prompt_tokens"), ev.get("output_tokens"),
                        ev.get("ms"), ev.get("done_reason"), Jsonb(ev.get("first_token") or []), Jsonb(ev.get("request") or {})))
        elif ev.get("type") == "mcp":
            self._exec("INSERT INTO mcp_messages(run_id,server,direction,method,rpc_id,msg,bytes) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                       (run_id, ev["server"], ev["dir"], ev.get("method"),
                        None if ev.get("rpc_id") is None else str(ev["rpc_id"]), Jsonb(ev["msg"]), ev.get("bytes")))
        elif ev.get("type") == "tool":
            self._exec("INSERT INTO tool_calls(run_id,seq,name,args,result,ok,ms)"
                       " VALUES(%s,(SELECT count(*)+1 FROM tool_calls WHERE run_id=%s),%s,%s,%s,%s,%s)",
                       (run_id, run_id, ev["name"], Jsonb(ev.get("args")), ev.get("content"), ev.get("ok", True), ev.get("ms")))

    def finish(self, run_id: str, status: str, answer: str | None = None, stop_reason: str | None = None) -> None:
        self._exec("UPDATE runs SET status=%s, answer=%s, stop_reason=%s, ended_at=localtimestamp WHERE id=%s",
                   (status, answer, stop_reason, run_id))

    def close(self) -> None:
        if self._store:
            self._store.close()


_default: RunLog | None = None


def default() -> RunLog:
    global _default
    if _default is None:
        _default = RunLog()
    return _default


def set_default(r: RunLog | None) -> None:
    """테스트에서 임시 스키마로 바꿀 때 쓴다."""
    global _default
    _default = r
