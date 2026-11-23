# Skills

특정 일을 하는 절차서를 필요할 때만 불러온다. MCP는 "무엇에 닿을 수 있나(연결)", Skill은 "그걸로 어떻게 하나(절차)".

## 파일

```
chat_ui/skills/<이름>/SKILL.md
---
name: notion-page-summary
description: Notion에 있는 페이지를 제목으로 찾아 내용을 요약한다      ← 평소 프롬프트에는 이것만
allowed-tools: [API-post-search, API-retrieve-page-markdown]           ← 불러오면 도구를 이것만 남긴다
---
# 절차 본문 (마크다운)                                                   ← 불러올 때 대화에 들어간다
```

들어 있는 Skill: `notion-page-summary`, `monthly-report`.

## 동작 (`chat_ui/agent/skills.py`)

1. 시스템 프롬프트에 Skill 한 줄 목록과 규칙("질문이 설명에 맞으면 먼저 load_skill")을 붙인다.
2. 도구는 `load_skill` + 기본 도구(비서 코드·그룹웨어 MCP·time). Notion·filesystem 도구는 Skill 뒤에 숨는다.
3. 모델이 `load_skill(name)`을 부르면 절차 본문이 도구 결과로 대화에 들어가고, 다음 호출부터 도구가 그 Skill의 `allowed-tools`만 남는다.

패널 ⚙ "장별 상태"를 5장으로 두면 켜진다(기본). 타임라인에 `📘 … 불러옴 · 도구 15개 → 2개`.

## 실제로 돌려 본 결과 (KANANA 1.5 8B)

- Skill 고르기는 맞았다. 프롬프트는 Skill 없이 도구를 전부 붙였을 때보다 절반 이하로 줄었다.
- 불러온 절차를 따르지는 못했다(말로 계획만 하거나, 사용자에게 되묻거나, 도구 없이 지어냄). Skill은 모델이 절차를 따를 수 있다는 전제 위에 있다.
