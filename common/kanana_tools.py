"""KANANA 도구 호출 출력 해석.

KANANA 1.5 8B의 chat template은 도구를 부를 때 본문에 이렇게 쓰게 한다.

    <function=함수이름>{"인자": "값"}</function>

Ollama가 이것을 tool_calls로 바꿔 주지 못하면, 본문에 글자로 남는다.
이 모듈은 그 글자를 찾아 (이름, 인자 dict) 목록으로 바꾼다.
vLLM의 tool parser(functionary_v3_llama_31)가 하는 일을 코드 한 곳에서 하는 것이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_CALL = re.compile(r"<function=([A-Za-z0-9_\-\.]+)>(.*?)</function>", re.DOTALL)
# 닫는 태그 없이 끝난 경우 (stop 토큰에서 잘림)
_OPEN_TAIL = re.compile(r"<function=([A-Za-z0-9_\-\.]+)>(\{.*\})\s*$", re.DOTALL)


@dataclass
class ParsedCalls:
    calls: list[dict] = field(default_factory=list)   # [{"name":..., "args":{...}}]
    errors: list[str] = field(default_factory=list)   # 형식은 맞는데 JSON이 깨진 경우 등
    text: str = ""                                    # 호출 부분을 뺀 나머지 본문


def parse_function_calls(content: str) -> ParsedCalls:
    out = ParsedCalls()
    if not content:
        return out
    matches = list(_CALL.finditer(content))
    rest = content
    if not matches:
        tail = _OPEN_TAIL.search(content)
        if tail:
            matches = [tail]
    for m in matches:
        name, raw = m.group(1), m.group(2).strip()
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                raise ValueError("인자가 JSON 객체가 아님")
            out.calls.append({"name": name, "args": args})
        except (json.JSONDecodeError, ValueError) as e:
            out.errors.append(f"{name}: 인자 해석 실패 ({e}) — {raw[:120]}")
        rest = rest.replace(m.group(0), "")
    out.text = rest.strip()
    return out
