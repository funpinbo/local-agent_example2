r"""4편 1장 실습 — 같은 일을 기다려서 받기 / 흘려서 받기 / 물어서 받기.

A. 모델: 같은 프롬프트를 Ollama에 stream=false(다 쓸 때까지 기다림)와 stream=true(한 조각씩)로 보내
   첫 글자가 오기까지와 끝까지 걸린 시간을 잰다.
B. 비서: 같은 질문 하나를 폴링(0.9초마다 묻기, 3편 방식)과 SSE(연결 하나)로 받아
   HTTP 요청 수와 받은 이벤트 수를 센다.

    $env:PYTHONIOENCODING="utf-8"; .\.venv\Scripts\python.exe steps\p0_communication\01_wait_stream_poll.py
먼저 Ollama, 그룹웨어(8100), 비서(8200)를 띄운다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chat_ui.agent import assistant, toolcall  # noqa: E402
from common import config  # noqa: E402

AGENT = "http://127.0.0.1:8200/chat/api"
QUESTION = "10월에 OOM 장애가 있었어?"


def model_wait_vs_stream(prompt: str) -> dict:
    base = {"model": config.LLM_MODEL, "prompt": prompt, "raw": True,
            "options": {"temperature": 0, "num_ctx": config.LLM_NUM_CTX, "num_predict": 300, "stop": toolcall.STOP}}
    # 먼저 한 번 돌려 둔다: 모델 올리기·프롬프트 캐시 차이가 두 방식 비교에 섞이지 않게
    httpx.post(f"{config.OLLAMA_BASE_URL}/api/generate", json={**base, "stream": False}, timeout=600)
    t0 = time.perf_counter()
    r = httpx.post(f"{config.OLLAMA_BASE_URL}/api/generate", json={**base, "stream": False}, timeout=600).json()
    wait_total = time.perf_counter() - t0
    t0, first, chunks = time.perf_counter(), None, 0
    with httpx.stream("POST", f"{config.OLLAMA_BASE_URL}/api/generate", json={**base, "stream": True}, timeout=600) as s:
        for line in s.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            if d.get("response") and first is None:
                first = time.perf_counter() - t0
            chunks += 1
    stream_total = time.perf_counter() - t0
    return {"output_tokens": r.get("eval_count"), "wait_total_s": round(wait_total, 2),
            "stream_first_s": round(first or 0, 2), "stream_total_s": round(stream_total, 2), "stream_chunks": chunks}


def run_poll() -> dict:
    job = httpx.post(f"{AGENT}/run", json={"key": "assistant", "text": QUESTION, "stage": "1"}).json()
    since, requests, events, t0 = 0, 1, 0, time.perf_counter()
    first_event = None
    while True:
        v = httpx.get(f"{AGENT}/run/{job['id']}?since={since}").json()
        requests += 1
        if v["events"] and first_event is None:
            first_event = time.perf_counter() - t0
        events += len(v["events"])
        since = v["next"]
        if v["status"] in ("done", "error"):
            break
        time.sleep(0.9)
    return {"job": job["id"], "http_requests": requests,
            "events": events, "first_event_s": round(first_event or 0, 2), "total_s": round(time.perf_counter() - t0, 1)}


def run_sse() -> dict:
    job = httpx.post(f"{AGENT}/run", json={"key": "assistant", "text": QUESTION, "stage": "1"}).json()
    t0, first_event, steps, statuses, kb = time.perf_counter(), None, 0, 0, 0
    with httpx.stream("GET", f"{AGENT}/run/{job['id']}/events", timeout=None) as s:
        for line in s.iter_lines():
            kb += len(line) + 1
            if line.startswith("event: step"):
                steps += 1
                first_event = first_event or (time.perf_counter() - t0)
            elif line.startswith("event: status"):
                statuses += 1
    return {"job": job["id"], "http_requests": 2, "events": steps, "status_events": statuses,
            "first_event_s": round(first_event or 0, 2), "total_s": round(time.perf_counter() - t0, 1),
            "bytes": kb}


def main() -> None:
    prompt = toolcall.render(assistant.messages_for("KV 캐시가 뭔지 세 문장으로 설명해 줘"), [])
    out = {"question_for_model": "KV 캐시가 뭔지 세 문장으로 설명해 줘", "model": model_wait_vs_stream(prompt)}
    m = out["model"]
    print(f"[모델] 출력 {m['output_tokens']}토큰 — 기다려서 받기: {m['wait_total_s']}초 뒤 한 번에 / "
          f"흘려서 받기: 첫 글자 {m['stream_first_s']}초, 끝 {m['stream_total_s']}초 ({m['stream_chunks']}조각)")
    out["poll"] = run_poll()
    p = out["poll"]
    print(f"[비서·폴링] '{QUESTION}' — HTTP 요청 {p['http_requests']}번, 이벤트 {p['events']}개, "
          f"첫 이벤트 {p['first_event_s']}초, 끝 {p['total_s']}초")
    out["sse"] = run_sse()
    s = out["sse"]
    print(f"[비서·SSE]  같은 질문 — HTTP 요청 {s['http_requests']}번(실행 시작 1 + 연결 1), 이벤트 {s['events']}개, "
          f"첫 이벤트 {s['first_event_s']}초, 끝 {s['total_s']}초")
    path = config.LOG_DIR / f"p0_01_wait_stream_poll_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("로그:", path)


if __name__ == "__main__":
    main()
