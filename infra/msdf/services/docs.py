"""docs 서비스: 사내 문서 적재·조각내기·색인·검색.

검색은 BM25(글자 2-gram + 단어)라 임베딩 모델 없이 동작한다. 조각마다 chunk_id와 source(원본 파일)를 남긴다.
같은 문서 묶음에 두 번째 질문이 오면 load·chunk·index는 MSDF 캐시를 쓰고 query·search만 다시 돈다.

remote 모드:  python infra/msdf/services/docs.py   (msdf up이 먼저 떠 있어야 한다)
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

from ms_dataflow import action, invalidate, service, start

service("docs")

DOCS_ROOT = Path(os.getenv("DOCS_ROOT", Path(__file__).resolve().parents[3] / "common" / "sample_data" / "docs"))
K1, B = 1.5, 0.75


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[0-9A-Za-z가-힣\-\.]+", text.lower())
    grams = []
    for w in words:
        if re.search(r"[가-힣]", w) and len(w) > 1:
            grams += [w[i:i + 2] for i in range(len(w) - 1)]
    return words + grams


def _sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    title, parts, head, buf = "", [], "", []
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            if "\n".join(buf).strip():          # 첫 소제목 앞의 빈 줄로 빈 조각을 만들지 않는다
                parts.append((head, "\n".join(buf).strip()))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf and "\n".join(buf).strip():
        parts.append((head, "\n".join(buf).strip()))
    return title, parts


@action(params=["corpus"], outputs=["docs"])
def load(corpus: str) -> list:
    folder = DOCS_ROOT / corpus
    if not folder.is_dir():
        raise FileNotFoundError(f"문서 묶음이 없습니다: {corpus}")
    return [{"source": p.name, "text": p.read_text(encoding="utf-8")} for p in sorted(folder.glob("*.md"))]


@action(inputs=["docs"], outputs=["chunks"])
def chunk(docs: list) -> list:
    out = []
    for d in docs:
        title, parts = _sections(d["text"])
        stem = d["source"].rsplit(".", 1)[0]
        for i, (head, body) in enumerate(parts):
            out.append({"chunk_id": f"{stem}#{i}", "source": d["source"], "title": title,
                        "heading": head, "text": body})
    return out


@action(inputs=["chunks"], outputs=["index"])
def index(chunks: list) -> dict:
    toks = [_tokens(f"{c['title']} {c['heading']} {c['text']}") for c in chunks]
    df = Counter(t for ts in toks for t in set(ts))
    avgdl = sum(len(t) for t in toks) / max(len(toks), 1)
    return {"chunks": chunks, "tf": [dict(Counter(t)) for t in toks],
            "len": [len(t) for t in toks], "df": dict(df), "avgdl": avgdl}


@action(params=["q", "k"], outputs=["query"])
def query(q: str, k: int) -> dict:
    return {"q": q, "k": int(k)}


@action(inputs=["index", "query"], outputs=["hits"])
def search(index: dict, query: dict) -> list:
    n = len(index["chunks"])
    scores = []
    for i, tf in enumerate(index["tf"]):
        s = 0.0
        for t in set(_tokens(query["q"])):
            if t not in tf:
                continue
            idf = math.log(1 + (n - index["df"][t] + 0.5) / (index["df"][t] + 0.5))
            f = tf[t]
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * index["len"][i] / index["avgdl"]))
        scores.append((s, i))
    scores.sort(reverse=True)
    hits = []
    for s, i in scores[: query["k"]]:
        if s <= 0:
            break
        c = index["chunks"][i]
        hits.append({**c, "score": round(s, 3)})
    return hits


@action(inputs=["docs"], outputs=["catalog"])
def catalog(docs: list) -> list:
    out = []
    for d in docs:
        title, parts = _sections(d["text"])
        out.append({"source": d["source"], "title": title, "headings": [h for h, _ in parts]})
    return out


MEMO_ROOT = DOCS_ROOT.parent                     # common/sample_data (메모 파일)
UPLOAD_STORE = DOCS_ROOT / "_uploaded_files"      # 챗봇에서 올린 문서(마크다운으로 정리한 것)


@action(params=["name"], outputs=["memo"])
def memo(name: str) -> str:
    """1장 실습 메모. 파일 이름만 받는다(폴더 밖은 읽지 않는다)."""
    path = MEMO_ROOT / Path(name).name
    if path.suffix != ".txt" or not path.exists():
        raise FileNotFoundError(f"메모가 없습니다: {name}")
    return path.read_text(encoding="utf-8")


@action(params=["name", "text"], outputs=["stored"])
def ingest(name: str, text: str) -> dict:
    """올린 문서를 문서 저장소에 넣는다. 같은 이름·내용이면 다시 써도 결과가 같다(멱등)."""
    UPLOAD_STORE.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_STORE / Path(name).name
    path.write_text(text, encoding="utf-8")
    title, parts = _sections(text)
    return {"name": path.name, "chars": len(text), "title": title, "sections": len(parts)}


def watch_docs(interval: float = 2.0) -> threading.Thread:
    """문서 파일이 바뀌면 invalidate("load"). 하위 단계(chunk·index·search)는 입력이 바뀌어 저절로 다시 계산된다."""
    def snapshot():
        return {str(p): p.stat().st_mtime for p in DOCS_ROOT.rglob("*.md")}

    def loop():
        last = snapshot()
        while True:
            time.sleep(interval)
            now = snapshot()
            if now != last:
                last = now
                invalidate("load")
                print("[docs] 문서 변경 → invalidate(load)", flush=True)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    start()
    watch_docs()
    print(f"[docs] 문서 위치 {DOCS_ROOT}", flush=True)
    while True:
        time.sleep(3600)
