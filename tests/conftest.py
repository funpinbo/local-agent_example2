"""여러 테스트가 같이 쓰는 준비물."""

import socket
import uuid
import threading
import time

import pytest
import uvicorn

from common import config
from infra.groupware import db as gw_db
from infra.groupware.app import create_app
from infra.groupware.seed import seed
from infra.msdf.services import agent, groupware_sync


@pytest.fixture()
def pg_schema():
    """테스트마다 PostgreSQL에 임시 스키마를 만들고 샘플 데이터를 넣는다. 끝나면 지운다.
    Postgres가 떠 있어야 한다: docker compose -f infra/docker-compose.yml up -d"""
    schema = f"gw_test_{uuid.uuid4().hex[:10]}"
    try:
        seed(gw_db.DSN, schema)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"PostgreSQL에 붙지 못했습니다({gw_db.DSN}): {e}")
    yield schema
    gw_db.drop_schema(gw_db.DSN, schema)


@pytest.fixture()
def gw_env(tmp_path, monkeypatch, pg_schema):
    """샘플 데이터를 넣은 그룹웨어 서버(임의 포트) + MSDF local + 출력 폴더. 서버 주소를 돌려준다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(schema=pg_schema), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(groupware_sync, "GROUPWARE_URL", url)
    monkeypatch.setattr(config, "GROUPWARE_URL", url)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    agent.close()
    agent.connect("local", db=tmp_path / "msdf.db")
    yield url
    agent.close()
    server.should_exit = True
    th.join(5)


@pytest.fixture(autouse=True)
def _no_real_mcp():
    """업무 비서 테스트가 servers.yaml의 실제 서버(npx 등)를 띄우지 않게 기본으로 MCP를 끈다."""
    from chat_ui import runner
    runner.set_mcp_host(None, disabled=True)
    yield
    runner.set_mcp_host(None, disabled=True)
