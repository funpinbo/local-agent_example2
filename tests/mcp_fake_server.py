"""테스트용 MCP 서버 (stdio). 읽기 도구 2개, 쓰기 도구 1개.

    python tests/mcp_fake_server.py
"""

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("fake-office", version="0.1")
RO = ToolAnnotations(read_only_hint=True)


@server.tool(annotations=RO)
def add(a: int, b: int) -> str:
    """두 수를 더한다."""
    return str(a + b)


@server.tool(annotations=RO)
def team_of(user: str) -> str:
    """사용자 아이디로 팀 이름을 알려 준다."""
    return {"jaehwi": "AI플랫폼팀", "taeyang": "보안팀"}.get(user, "모름")


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def send_note(to: str, text: str) -> str:
    """메모를 보낸다(쓰기)."""
    return f"{to}에게 보냈습니다"


if __name__ == "__main__":
    server.run("stdio")
