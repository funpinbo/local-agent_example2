"""python -m chat_ui.mcp_servers.groupware  [--port 8301]"""

import argparse
import os

import uvicorn

from chat_ui.agent.mcp_host import load_env_file
from chat_ui.mcp_servers.groupware.server import app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8301)
    args = ap.parse_args()
    for k, v in load_env_file().items():          # .env의 GROUPWARE_MCP_KEY
        os.environ.setdefault(k, v)
    if not os.getenv("GROUPWARE_MCP_KEY"):
        print("경고: GROUPWARE_MCP_KEY가 없어 서비스 키 확인 없이 뜹니다.")
    uvicorn.run(app(), host="127.0.0.1", port=args.port)
