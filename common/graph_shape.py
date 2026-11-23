"""컴파일한 LangGraph의 모양 확인: 순환이 있는가(DAG/Cyclic), Mermaid 그림 글."""

from __future__ import annotations


def edges(graph) -> list[tuple[str, str]]:
    g = graph.get_graph()
    return [(e.source, e.target) for e in g.edges]


def cycles(graph) -> list[list[str]]:
    """노드 순환 목록(같은 순환은 한 번만)."""
    adj: dict[str, list[str]] = {}
    for a, b in edges(graph):
        adj.setdefault(a, []).append(b)
    found, seen = [], set()

    def dfs(node, path):
        for nxt in adj.get(node, []):
            if nxt in path:
                cyc = path[path.index(nxt):]
                key = frozenset(cyc)
                if key not in seen:
                    seen.add(key)
                    found.append(cyc + [nxt])
            else:
                dfs(nxt, path + [nxt])

    for start in adj:
        dfs(start, [start])
    return found


def kind(graph) -> str:
    return "Cyclic" if cycles(graph) else "DAG"


def mermaid(graph) -> str:
    return graph.get_graph().draw_mermaid()
