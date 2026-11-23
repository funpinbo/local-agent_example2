r"""4편 1장 실습 2 — 도구를 부를지 말지는 첫 토큰에서 갈린다.

도구 11개를 붙인 같은 프롬프트에 질문만 바꿔 보내고, 모델이 쓴 첫 토큰과 그 확률, 후보를 남긴다.
도구가 필요한 질문 / 필요 없는 질문 / 애매한 질문을 섞었다. temperature 0.

    $env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe steps\p1_tool_calling\02_first_token.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chat_ui.agent import assistant, toolcall  # noqa: E402
from common import config  # noqa: E402

QUESTIONS = [
    ("도구 필요", "10월 12일에 받은 메일 보여 줘"),
    ("도구 필요", "보안팀이 반려한 결재 사유가 뭐야?"),
    ("도구 필요", "OOM이 났을 때 대응 방법이 사내 문서에 있어?"),
    ("도구 불필요", "안녕! 너는 뭘 할 수 있어?"),
    ("도구 불필요", "KV 캐시가 뭔지 한 문장으로 설명해 줘"),
    ("애매", "요즘 내가 바빴어?"),
]


def main() -> None:
    llm = toolcall.RawKanana(temperature=0, num_predict=80)
    tools = assistant.tools()
    rows = []
    for kind, q in QUESTIONS:
        out = llm.generate(toolcall.render(assistant.messages_for(q), tools))
        parsed = toolcall.parse_function_calls(out.text)
        rows.append({"kind": kind, "question": q, "first_token": out.first_token, "output": out.text,
                     "called": [c["name"] for c in parsed.calls], "errors": parsed.errors})
        top = out.first_token[0] if out.first_token else {}
        alts = ", ".join(f"{a['token']!r} {a['p']:.1%}" for a in out.first_token[1:4])
        print(f"[{kind}] {q}\n   첫 토큰 {top.get('token')!r} {top.get('p', 0):.2%}  (후보 {alts})\n"
              f"   → {'호출 ' + ', '.join(rows[-1]['called']) if parsed.calls else '말로 답함'}: {out.text[:80]!r}\n")
    out_path = config.LOG_DIR / f"p1_02_first_token_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps({"model": config.LLM_MODEL, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"로그: {out_path}")


if __name__ == "__main__":
    main()
