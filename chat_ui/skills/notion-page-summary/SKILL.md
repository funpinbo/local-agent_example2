---
name: notion-page-summary
description: Notion에 있는 페이지를 제목으로 찾아 내용을 요약한다
allowed-tools: [API-post-search, API-retrieve-page-markdown]
---
# Notion 페이지 요약

페이지 id는 절대 지어내지 않는다. id는 반드시 검색 결과에서 가져온다.

1. `API-post-search`로 찾는다. 인자는 `{"query": "페이지 제목의 핵심 단어"}` 하나면 된다.
2. 결과의 `results`에서 제목이 질문과 가장 가까운 항목의 `id`를 고른다. 여러 개면 가장 최근(`last_edited_time`) 것.
   - 0건이면 검색어를 더 짧게 바꿔 한 번 더 찾는다. 그래도 없으면 "찾지 못했다"고 답한다.
3. `API-retrieve-page-markdown`에 `{"page_id": "<고른 id>"}`로 본문을 읽는다.
4. 본문을 5줄 안쪽으로 요약한다. 첫 줄에 페이지 제목을 쓰고, 마지막에 근거로 `[notion:<id 앞 8자리>]`를 단다.
