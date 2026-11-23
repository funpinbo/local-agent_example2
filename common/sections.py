"""4단계 월간 보고 작성 도우미.

3단계 04에서 근거가 맞는 보고가 나온 방식(섹션마다 일반 호출, 앞 섹션을 보여 주기)을 그대로 쓴다.
담당 Agent의 메모 대신 그룹웨어 기록 원문(gw_month digest의 항목)을 근거로 준다.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

KIND = {"mails": "메일", "events": "일정", "approvals": "결재"}
SECTIONS = ["주요 업무", "장애와 대응", "결재 진행", "다음 달 계획"]


def items_of(digest: dict, kinds: list[str] | None = None) -> list[dict]:
    labels = {KIND[k] for k in (kinds or KIND)}
    return [i for i in digest["items"] if i["kind"] in labels]


def items_text(items: list[dict]) -> str:
    return "\n".join(f"[{i['ref']}] {i['at'][:10]} {i['kind']}: {i['text']}" for i in items) or "(기록 없음)"


def write_section(llm, title: str, heading: str, items: list[dict], before: list[dict] | None = None,
                  guide: str = "", feedback: str = "") -> str:
    prev = "\n\n".join(f"## {s['heading']}\n{s['body']}" for s in (before or [])) or "(아직 없음)"
    ask = (f"보고서 '{title}'의 섹션 '{heading}'을 3~5문장으로 쓴다. 앞 섹션과 겹치는 내용은 쓰지 않는다. {guide}\n"
           f"아래 기록만 근거로, 문장마다 기록의 id를 [mail:12]처럼 그대로 단다.\n\n[앞 섹션]\n{prev}\n\n[기록]\n{items_text(items)}")
    if feedback:
        ask += f"\n\n[고칠 점]\n{feedback}"
    return llm.invoke([SystemMessage("너는 월간 보고의 한 섹션을 쓴다. 본문만 출력한다."), HumanMessage(ask)]).content
