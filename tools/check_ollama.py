"""Ollama로 띄운 KANANA의 도구 호출 확인.

같은 질문 세트를 여러 번 보내고, 도구 호출이 어떤 경로로 나오는지 센다.
  - native : Ollama 응답의 message.tool_calls에 들어옴 (Ollama가 해석함)
  - text   : 본문에 <function=...>...</function> 글자로 남음 (우리 코드가 해석해야 함)
  - none   : 도구 호출 없음

실행:  python -m tools.check_ollama [--model kanana-1.5-8b-q8] [--runs 5]
결과:  blog/logs/check_ollama_<모델>_<시각>.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from common.kanana_tools import parse_function_calls

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://localhost:11434"

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "도시의 현재 날씨를 조회한다",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string", "description": "도시 이름"}},
                       "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "search_docs",
        "description": "사내 문서에서 질문과 관련된 내용을 검색한다",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string", "description": "검색어"},
                                      "k": {"type": "integer", "description": "가져올 개수"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "save_report",
        "description": "보고서 칸을 채워 docx로 저장한다",
        "parameters": {"type": "object",
                       "properties": {"template": {"type": "string", "enum": ["weekly", "trip"]},
                                      "fields": {"type": "object", "description": "칸 이름과 내용"}},
                       "required": ["template", "fields"]}}},
]

# (이름, 메시지, 기대: 도구 이름 또는 None)
CASES = [
    ("one_tool", [{"role": "user", "content": "대전 날씨 알려줘."}], "get_weather"),
    ("pick_tool", [{"role": "user", "content": "사내 문서에서 vLLM OOM 원인 찾아줘."}], "search_docs"),
    ("nested_args", [{"role": "user", "content":
        "주간 보고서로 저장해 줘. 이번 주 한 일: 로컬 LLM 서버 설치. 다음 주 계획: 부하 테스트."}], "save_report"),
    ("no_tool", [{"role": "user", "content": "LLM과 Agent의 차이를 한 문장으로 설명해 줘."}], None),
    ("after_result", [
        {"role": "user", "content": "대전 날씨 알려줘."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "get_weather", "arguments": {"city": "대전"}}}]},
        {"role": "tool", "content": '{"city": "대전", "weather": "맑음", "temp_c": 14}'},
    ], None),
]


def classify(msg: dict, expect: str | None) -> dict:
    native = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    parsed = parse_function_calls(content)
    if native:
        path, names = "native", [c["function"]["name"] for c in native]
    elif parsed.calls:
        path, names = "text", [c["name"] for c in parsed.calls]
    else:
        path, names = "none", []
    ok = (names[:1] == [expect]) if expect else (not names and bool(content.strip()))
    return {"path": path, "names": names, "ok": ok,
            "parse_errors": parsed.errors, "content": content[:400],
            "native": native}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="kanana-1.5-8b-q8")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    version = httpx.get(f"{BASE}/api/version", timeout=10).json().get("version")
    results, summary = [], {}
    with httpx.Client(timeout=300) as client:
        for name, messages, expect in CASES:
            rows = []
            for i in range(args.runs):
                t0 = time.perf_counter()
                r = client.post(f"{BASE}/api/chat", json={
                    "model": args.model, "messages": messages, "tools": TOOLS,
                    "stream": False, "options": {"temperature": 0.2}})
                r.raise_for_status()
                body = r.json()
                row = classify(body["message"], expect)
                row["sec"] = round(time.perf_counter() - t0, 2)
                row["eval_count"] = body.get("eval_count")
                rows.append(row)
                print(f"{name:12s} #{i+1} path={row['path']:6s} names={row['names']} ok={row['ok']} {row['sec']}s")
            summary[name] = {
                "expect": expect,
                "ok": sum(r["ok"] for r in rows),
                "native": sum(r["path"] == "native" for r in rows),
                "text": sum(r["path"] == "text" for r in rows),
                "none": sum(r["path"] == "none" for r in rows),
                "runs": args.runs,
            }
            results.append({"case": name, "expect": expect, "rows": rows})

    out = {"model": args.model, "ollama": version, "at": datetime.now().isoformat(timespec="seconds"),
           "summary": summary, "results": results}
    logdir = ROOT / "blog" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / f"check_ollama_{args.model}_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("saved", path)


if __name__ == "__main__":
    main()
