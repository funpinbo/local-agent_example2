"""사내 문서 검색. 에이전트는 MSDF에 결과물 이름(doc_search, doc_catalog)으로 요청만 한다.

어떤 서비스가 어떤 순서로 문서를 읽고 색인하는지는 MSDF DAG가 정한다(infra/msdf/dags).
"""

from __future__ import annotations

import json
import os

from langchain.tools import tool

from infra.msdf.services import agent

CORPUS = os.getenv("DOC_CORPUS", "llm_server")


def search(q: str, k: int = 4) -> list[dict]:
    return agent.fetch("doc_search", {"corpus": CORPUS, "q": q, "k": int(k)})


def catalog() -> list[dict]:
    return agent.fetch("doc_catalog", {"corpus": CORPUS})


def catalog_text() -> str:
    return "\n".join(f"- {c['source']}: {c['title']} (소제목: {', '.join(c['headings'])})" for c in catalog())


@tool
def doc_search(query: str, k: int = 4) -> str:
    """사내 문서(로컬 LLM 서버 운영 문서)에서 query와 관련된 조각을 k개 찾는다. 결과의 chunk_id를 본문 인용 [chunk_id]에 쓴다."""
    hits = search(query, k)
    if not hits:
        return "찾은 조각이 없습니다. 다른 검색어로 찾아보세요."
    return json.dumps([{"chunk_id": h["chunk_id"], "source": h["source"], "heading": h["heading"], "text": h["text"]}
                       for h in hits], ensure_ascii=False)


@tool
def doc_catalog() -> str:
    """사내 문서 목록과 각 문서의 소제목을 본문 없이 보여 준다. 목차를 짤 때 쓴다."""
    return catalog_text()
