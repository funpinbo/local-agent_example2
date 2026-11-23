"""그룹웨어 저장소 (PostgreSQL). 실습용이라 인증 없이 X-User 헤더로 사용자를 구분한다.

- 접속: GROUPWARE_DSN, 스키마: GROUPWARE_SCHEMA (기본 groupware). 테스트는 임시 스키마를 만들어 쓴다.
- 스키마는 infra/db/migrations/groupware/*.sql 을 순서대로 적용한다.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.getenv("GROUPWARE_DSN", "postgresql://groupware:groupware@127.0.0.1:15432/office")
SCHEMA = os.getenv("GROUPWARE_SCHEMA", "groupware")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "groupware"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def check_schema(schema: str) -> str:
    if not _IDENT.match(schema):
        raise ValueError(f"스키마 이름이 이상합니다: {schema!r}")
    return schema


def migrate(dsn: str = DSN, schema: str = SCHEMA, *, reset: bool = False, migrations: Path = MIGRATIONS) -> list[str]:
    """스키마를 만들고(reset이면 지우고 다시) 아직 안 돈 마이그레이션을 적용한다. 적용한 파일 이름을 돌려준다.
    migrations: SQL 파일 폴더 (그룹웨어 기본, 비서는 infra/db/migrations/agent)"""
    check_schema(schema)
    applied = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        if reset:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"SET search_path TO {schema}")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamp DEFAULT localtimestamp)")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in sorted(Path(migrations).glob("*.sql")):
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied


def drop_schema(dsn: str, schema: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {check_schema(schema)} CASCADE")


class Store:
    """연결 풀 하나. `with store.tx() as c:` 안에서 c.execute(...)를 쓰고, 블록이 끝나면 커밋된다."""

    def __init__(self, dsn: str = DSN, schema: str = SCHEMA, *, max_size: int = 8):
        self.dsn, self.schema = dsn, check_schema(schema)
        self.pool = ConnectionPool(
            dsn, min_size=1, max_size=max_size, open=True,
            kwargs={"row_factory": dict_row, "options": f"-c search_path={schema}"})

    @contextmanager
    def tx(self):
        with self.pool.connection() as conn:     # 예외 없이 끝나면 commit, 예외면 rollback
            yield conn

    def all(self, sql: str, args=None) -> list[dict]:
        with self.tx() as c:
            return [row(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql: str, args=None) -> dict | None:
        with self.tx() as c:
            return row(c.execute(sql, args).fetchone())

    def close(self) -> None:
        self.pool.close()


def iso(v):
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    return v


def row(r: dict | None) -> dict | None:
    """DB 행 → API 응답 dict. 시각은 3편과 같은 '2026-10-01T09:10:00' 문자열로."""
    if r is None:
        return None
    return {k: iso(v) for k, v in r.items()}
