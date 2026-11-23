r"""4편 블로그 시나리오를 실제 모델로 돌리고 로그를 남긴다.

    $env:PYTHONIOENCODING="utf-8"
    .\.venv\Scripts\python.exe steps\p4_scenarios.py --list            # 시나리오 목록
    .\.venv\Scripts\python.exe steps\p4_scenarios.py p3_reject p5_notion
    .\.venv\Scripts\python.exe steps\p4_scenarios.py --chapter 5        # 한 장 전부
    .\.venv\Scripts\python.exe steps\p4_scenarios.py --all

먼저 띄워 둘 것: PostgreSQL(docker), Ollama, 그룹웨어(8100), 비서(8200), 그룹웨어 MCP(8301).
결과: blog/logs/p4_<id>_<시각>.json (이벤트 전부 + 요약). 같은 실행이 DB agent.runs에도 남는다(job id).
쓰기 시나리오(메일·일정)를 돌리면 끝에 그룹웨어 샘플 데이터를 다시 넣는다(3편 데이터 보존). --keep-data로 끈다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import config  # noqa: E402

AGENT = "http://127.0.0.1:8200/chat/api"
SCENARIOS = Path(__file__).with_suffix(".yaml")


def load() -> list[dict]:
    return yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]


def check_servers() -> None:
    for name, url in [("그룹웨어", f"{config.GROUPWARE_URL}/api/users"), ("비서", f"{AGENT}/catalog"),
                      ("Ollama", f"{config.OLLAMA_BASE_URL}/api/tags")]:
        try:
            httpx.get(url, timeout=5).raise_for_status()
        except Exception as e:  # noqa: BLE001
            sys.exit(f"{name} 서버에 붙지 못했습니다({url}): {e}")
    st = httpx.post(f"{AGENT}/mcp/connect", timeout=300).json()["servers"]
    print("MCP:", ", ".join(f"{s['name']} {s['status']}" for s in st))


def run(sc: dict) -> dict:
    body = {"key": "assistant", "text": sc["question"], "stage": str(sc.get("stage", 5)), "user": sc.get("user", "jaehwi")}
    job = httpx.post(f"{AGENT}/run", json=body, timeout=30).json()
    t0, decided = time.time(), 0
    while True:
        v = httpx.get(f"{AGENT}/run/{job['id']}", timeout=30).json()
        if v["status"] == "waiting" and (v.get("interrupt") or {}).get("kind") == "tool":
            plan = sc.get("approval", "reject")
            plan = plan if isinstance(plan, list) else [plan]
            act = plan[min(decided, len(plan) - 1)]          # 목록이면 차례대로, 끝나면 마지막 것을 반복
            ans = {"action": act, "comment": sc.get("comment", "") if act == "reject" else ""}
            if act == "edit":
                ans["args"] = sc.get("args")
            print(f"   ✋ {v['interrupt']['tool']} {json.dumps(v['interrupt']['args'], ensure_ascii=False)[:120]} → {act}")
            httpx.post(f"{AGENT}/run/{job['id']}/resume", json=ans, timeout=30)
            decided += 1
            time.sleep(1)
            continue
        if v["status"] in ("done", "error"):
            break
        if time.time() - t0 > 600:
            raise TimeoutError("10분이 넘었습니다")
        time.sleep(1.5)
    return {"job_id": job["id"], "seconds": round(time.time() - t0, 1), "decisions": decided, **v}


def summarize(v: dict) -> dict:
    ev = v["events"]
    tools_ev = next((e for e in ev if e["type"] == "tools"), {})
    srcs: dict[str, int] = {}
    for s in (tools_ev.get("sources") or {}).values():
        srcs[s] = srcs.get(s, 0) + 1
    return {
        "status": v["status"], "error": v.get("error"), "seconds": v["seconds"],
        "tools": len(tools_ev.get("names", [])), "tool_sources": srcs, "hidden_by_skill": tools_ev.get("hidden"),
        "llm": [{"n": e["n"], "prompt_tokens": e["prompt_tokens"], "output_tokens": e["output_tokens"], "ms": e["ms"],
                 "first": (e["first_token"] or [{}])[0], "output": e["output"][:300]} for e in ev if e["type"] == "llm_raw"],
        "calls": [{"name": e["name"], "args": e.get("args"), "ok": e.get("ok"), "result": str(e.get("content"))[:300]}
                  for e in ev if e["type"] == "tool"],
        "skills": [e["name"] for e in ev if e["type"] == "skill"],
        "approvals": [{k: e.get(k) for k in ("tool", "decision", "args", "final_args", "note")}
                      for e in ev if e["type"] == "approval"],
        "mcp_messages": sum(1 for e in ev if e["type"] == "mcp"),
        "answer": (v.get("result") or {}).get("final_answer"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep-data", action="store_true", help="쓰기 시나리오 뒤에 샘플 데이터를 다시 넣지 않는다")
    a = ap.parse_args()
    scs = load()
    if a.list:
        for s in scs:
            print(f"{s['id']:<16} {s['chapter']}장 stage {s.get('stage')}  {s['question']}")
        return
    pick = [s for s in scs if a.all or s["id"] in a.ids or (a.chapter and s["chapter"] == a.chapter)]
    if not pick:
        sys.exit("돌릴 시나리오를 고르세요 (--list로 목록)")
    check_servers()
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    wrote = False
    for sc in pick:
        print(f"\n▶ {sc['id']} ({sc['chapter']}장, stage {sc.get('stage')}) {sc['question']}")
        v = run(sc)
        sm = summarize(v)
        wrote |= any(x["decision"] == "approve" for x in sm["approvals"])
        for x in sm["llm"]:
            print(f"   LLM#{x['n']} 입력 {x['prompt_tokens']} → 출력 {x['output_tokens']}토큰 · 첫 토큰 "
                  f"{x['first'].get('token')!r} {x['first'].get('p', 0):.1%} · {x['output'][:90]!r}")
        for c in sm["calls"]:
            print(f"   ↩ {c['name']} ok={c['ok']} {json.dumps(c['args'], ensure_ascii=False)[:100]}")
        print(f"   답: {(sm['answer'] or '')[:200]!r}")
        out = config.LOG_DIR / f"p4_{sc['id']}_{datetime.now():%Y%m%d_%H%M%S}.json"
        out.write_text(json.dumps({"scenario": sc, "summary": sm, "job": v}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"   로그 {out.relative_to(ROOT)} · job {v['job_id']}")
    if wrote and not a.keep_data:
        print("\n쓰기 시나리오를 돌렸으므로 그룹웨어 샘플 데이터를 다시 넣는다(3편 데이터 보존).")
        subprocess.run([sys.executable, "-m", "infra.groupware.seed"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
