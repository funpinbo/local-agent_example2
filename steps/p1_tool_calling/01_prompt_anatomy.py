r"""4편 1장 실습 1 — 도구 정의가 프롬프트가 되는 모습과 비용.

같은 질문에 도구를 0 → 1 → 3 → 6 → 11개 붙여 가며
  - 원본 chat template으로 만든 프롬프트 글자 수와 토큰 수(Ollama prompt_eval_count)
  - 같은 대화를 Ollama /api/chat(Modelfile의 Go 템플릿)에 보냈을 때의 토큰 수
를 잰다. 도구 11개짜리 프롬프트 원문은 blog/logs/p1_01_prompt_11tools.txt로 남긴다.

    $env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe steps\p1_tool_calling\01_prompt_anatomy.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chat_ui.agent import assistant, toolcall  # noqa: E402
from common import config  # noqa: E402

QUESTION = "10월에 보안팀이 반려한 결재가 있었어?"
COUNTS = [0, 1, 3, 6, 11]


def raw_tokens(prompt: str) -> int:
    r = httpx.post(f"{config.OLLAMA_BASE_URL}/api/generate", timeout=300, json={
        "model": config.LLM_MODEL, "prompt": prompt, "raw": True, "stream": False,
        "options": {"num_predict": 1, "temperature": 0, "num_ctx": config.LLM_NUM_CTX}})
    return r.json()["prompt_eval_count"]


def chat_tokens(messages: list[dict], tools: list[toolcall.Tool]) -> int:
    r = httpx.post(f"{config.OLLAMA_BASE_URL}/api/chat", timeout=300, json={
        "model": config.LLM_MODEL, "messages": messages, "tools": [t.schema() for t in tools] or None,
        "stream": False, "options": {"num_predict": 1, "temperature": 0, "num_ctx": config.LLM_NUM_CTX}})
    return r.json()["prompt_eval_count"]


def main() -> None:
    all_tools = assistant.tools()
    msgs = assistant.messages_for(QUESTION)
    rows = []
    for n in COUNTS:
        ts = all_tools[:n]
        prompt = toolcall.render(msgs, ts)
        a, b = raw_tokens(prompt), raw_tokens(prompt)          # 두 번 재서 캐시 영향 확인
        rows.append({"tools": n, "names": [t.name for t in ts], "chars": len(prompt), "tokens_raw": a,
                     "tokens_raw_again": b, "tokens_ollama_chat": chat_tokens(msgs, ts)})
        if n == COUNTS[-1]:
            (config.LOG_DIR / "p1_01_prompt_11tools.txt").write_text(prompt, encoding="utf-8")
    base = rows[0]["tokens_raw"]
    print(f"질문: {QUESTION}\n")
    print(f"{'도구':>4} {'글자':>7} {'토큰(원본 템플릿)':>16} {'도구 몫':>8} {'토큰(/api/chat)':>15}")
    for r in rows:
        print(f"{r['tools']:>4} {r['chars']:>7,} {r['tokens_raw']:>16,} {r['tokens_raw'] - base:>8,} {r['tokens_ollama_chat']:>15,}")
    one, last = rows[1], rows[-1]
    per_tool = (last["tokens_raw"] - one["tokens_raw"]) / (last["tools"] - one["tools"])
    fixed = one["tokens_raw"] - base - per_tool
    print(f"\n도구가 하나라도 있으면 호출 형식 안내문이 붙는다: 약 {fixed:.0f}토큰(고정)."
          f"\n그 위로 도구 하나당 약 {per_tool:.0f}토큰. 매 LLM 호출마다 이만큼이 다시 들어간다.")
    if any(r["tokens_raw"] != r["tokens_raw_again"] for r in rows):
        print("주의: 같은 프롬프트를 두 번 쟀는데 토큰 수가 달랐다(Ollama 프롬프트 캐시).")
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = config.LOG_DIR / f"p1_01_prompt_anatomy_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"model": config.LLM_MODEL, "question": QUESTION, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"로그: {out}")


if __name__ == "__main__":
    main()
