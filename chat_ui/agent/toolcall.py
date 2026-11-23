"""Tool Calling 층 (4편 1장). 도구 호출 한 번이 모양을 네 번 바꾸는 과정을 전부 드러낸다.

    ① 도구 정의(JSON Schema)          Tool.schema()
    ② 모델 입력 글자                  render()  ← 원본 chat template(serving/kanana_chat_template.jinja)을 그대로 쓴다
    ③ 모델 출력 글자                  RawKanana.generate()  ← Ollama /api/generate, raw 모드
    ④ 호출(JSON) → 실행 → 결과 글자   parse_function_calls() → Tool.run() → tool 역할 메시지

3편은 Ollama /api/chat에 메시지와 도구를 넘겼다. 그러면 Ollama가 Modelfile의 Go 템플릿으로 ②를 만들고,
우리는 ②를 볼 수 없다. 여기서는 ②를 우리가 만들고 raw 모드로 보내서, 모델이 실제로 본 글자와 쓴 글자를 남긴다.

- Ollama는 raw 모드에서도 BOS 토큰을 스스로 붙인다 → 템플릿의 bos_token은 비워서 렌더링한다.
- 생성을 멈춘 토큰(<|eom_id|> / <|eot_id|>)은 응답에 오지 않는다. done_reason만 온다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import jinja2

from common import config
from common.kanana_tools import parse_function_calls

TEMPLATE_PATH = config.ROOT / "serving" / "kanana_chat_template.jinja"
STOP = ["<|eot_id|>", "<|eom_id|>", "<|start_header_id|>"]


# ---------------------------------------------------------------- ① 도구 정의

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                       # JSON Schema (type=object)
    fn: Callable[..., Any]
    writes: bool = False                   # 쓰기 도구는 사람 승인 대상 (MCP 장에서)
    source: str = "local"                  # local(비서 코드 안의 함수) | mcp:<서버 이름>

    def schema(self) -> dict:
        """OpenAI·Ollama API의 tools[] 한 칸과 같은 모양."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def run(self, args: dict) -> str:
        out = self.fn(**args)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)


def from_langchain(lc_tool, *, writes: bool = False) -> Tool:
    """3편의 @tool 함수를 그대로 가져온다(스키마는 함수 시그니처와 docstring에서 나온다)."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    f = convert_to_openai_tool(lc_tool)["function"]
    return Tool(f["name"], f.get("description", ""), f.get("parameters", {"type": "object", "properties": {}}),
                lambda **kw: lc_tool.invoke(kw), writes=writes)


# ---------------------------------------------------------------- ② 모델 입력 글자

_env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True)          # transformers의 apply_chat_template과 같은 설정
_env.filters["tojson"] = lambda v, **kw: json.dumps(v, ensure_ascii=False)
_template: jinja2.Template | None = None


def render(messages: list[dict], tools: list[Tool] | None = None, *, generation_prompt: bool = True) -> str:
    """messages: {"role": system|user|assistant|tool, "content": str, "tool_calls"?: [{"function": {"name", "arguments": str}}]}"""
    global _template
    if _template is None:
        _template = _env.from_string(Path(TEMPLATE_PATH).read_text(encoding="utf-8"))
    return _template.render(messages=messages, tools=[t.schema() for t in tools] if tools else None,
                            bos_token="", add_generation_prompt=generation_prompt)


# ---------------------------------------------------------------- ③ 모델 출력 글자

@dataclass
class LLMOutput:
    prompt: str
    text: str                              # 모델이 쓴 글자 그대로
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    ms: int = 0
    done_reason: str = ""
    first_token: list[dict] = field(default_factory=list)   # 첫 토큰 후보 [{token, p}] — "부를지 말지"가 갈리는 곳
    request: dict = field(default_factory=dict)              # HTTP 요청 본문(프롬프트는 길어서 뺀 것)


class RawKanana:
    """Ollama /api/generate, raw=true. 템플릿은 우리가 이미 적용했으므로 Ollama는 글자를 그대로 모델에 넣는다."""

    def __init__(self, model: str = config.LLM_MODEL, base_url: str = config.OLLAMA_BASE_URL,
                 temperature: float = config.LLM_TEMPERATURE, num_ctx: int = config.LLM_NUM_CTX,
                 num_predict: int = config.LLM_NUM_PREDICT, top_logprobs: int = 5):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.options = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": num_predict, "stop": STOP}
        self.top_logprobs = top_logprobs

    def generate(self, prompt: str) -> LLMOutput:
        body = {"model": self.model, "prompt": prompt, "raw": True, "stream": False, "options": self.options,
                "logprobs": True, "top_logprobs": self.top_logprobs}
        t0 = time.perf_counter()
        r = httpx.post(f"{self.base_url}/api/generate", json=body, timeout=600)
        r.raise_for_status()
        d = r.json()
        first = []
        if d.get("logprobs"):
            import math
            lp0 = d["logprobs"][0]
            # 고른 토큰을 먼저, 후보 목록을 뒤에. Ollama가 돌려주는 후보 글자는 고른 토큰과 모양이 다를 때가 있다
            # (고른 토큰 "<function", 후보 "function") → 둘 다 그대로 남긴다.
            first = [{"token": lp0["token"], "p": round(math.exp(lp0["logprob"]), 4), "chosen": True}]
            first += [{"token": a["token"], "p": round(math.exp(a["logprob"]), 4)} for a in lp0.get("top_logprobs", [])]
        return LLMOutput(prompt=prompt, text=d.get("response", ""), prompt_tokens=d.get("prompt_eval_count"),
                         output_tokens=d.get("eval_count"), ms=int((time.perf_counter() - t0) * 1000),
                         done_reason=d.get("done_reason", ""), first_token=first,
                         request={**{k: v for k, v in body.items() if k != "prompt"}, "prompt": f"({len(prompt)}자)"})


class ScriptedRaw:
    """테스트용 가짜 모델: 정해 둔 글자를 차례로 돌려준다. 함수면 프롬프트를 받아 글자를 만든다."""

    def __init__(self, outputs: list[str | Callable[[str], str]]):
        self.outputs, self.prompts = list(outputs), []

    def generate(self, prompt: str) -> LLMOutput:
        self.prompts.append(prompt)
        o = self.outputs.pop(0)
        text = o(prompt) if callable(o) else o
        return LLMOutput(prompt=prompt, text=text, prompt_tokens=len(prompt) // 3, output_tokens=len(text) // 3,
                         done_reason="stop", first_token=[{"token": text[:9], "p": 1.0}])


# ---------------------------------------------------------------- ④ 실행 루프

@dataclass
class LoopResult:
    answer: str
    stop_reason: str                       # answered | max_llm_calls | repeated_call
    messages: list[dict]
    llm_calls: int
    tool_calls: int


Approver = Callable[[Tool, dict], tuple[str, dict, str]]
"""쓰기 도구를 실행하기 전에 사람에게 묻는다. (결정 approve|reject, 실행할 인자(고쳤으면 고친 것), 의견)을 돌려준다."""


def run_loop(llm, messages: list[dict], tools: list[Tool] | Callable[[], list[Tool]], *,
             emit: Callable[[dict], None] = lambda e: None,
             approve: Approver | None = None,
             max_llm_calls: int = 8, max_same_call: int = 2, result_chars: int = 6000) -> LoopResult:
    """모델이 도구를 부르면 실행해서 결과를 tool 역할로 붙이고 다시 묻는다. 호출이 없으면 그 글이 답이다.

    멈추는 조건은 코드가 정한다(3편 1-4): LLM 호출 수 상한, 같은 호출 반복.
    잘못된 호출(없는 도구, 깨진 JSON, 실행 오류)은 멈추지 않고 오류 글을 결과로 돌려줘 모델이 고치게 한다.
    tools가 함수면 LLM을 부를 때마다 다시 읽는다(5장 Skill: 불러오면 도구가 바뀐다).
    """
    get_tools = tools if callable(tools) else (lambda: tools)
    seen: dict[str, int] = {}
    prev_prompt = ""
    n_llm = n_tool = 0
    while True:
        if n_llm >= max_llm_calls:
            return LoopResult("(LLM 호출 상한에 걸려 멈췄습니다)", "max_llm_calls", messages, n_llm, n_tool)
        current = get_tools()
        by_name = {t.name: t for t in current}
        prompt = render(messages, current)
        out = llm.generate(prompt)
        n_llm += 1
        # 이번에 새로 붙은 부분 = 지난 프롬프트 + 지난 출력 뒤로 붙은 글자
        delta = prompt[len(prev_prompt):] if prev_prompt and prompt.startswith(prev_prompt) else None
        parsed = parse_function_calls(out.text)
        emit({"type": "llm_raw", "node": "llm", "n": n_llm, "prompt": prompt, "prompt_delta": delta,
              "output": out.text, "prompt_tokens": out.prompt_tokens, "output_tokens": out.output_tokens,
              "ms": out.ms, "done_reason": out.done_reason, "first_token": out.first_token, "request": out.request})
        prev_prompt = prompt
        if not parsed.calls and not parsed.errors:
            emit({"type": "parsed", "node": "parser", "calls": [], "errors": [], "text": parsed.text,
                  "verdict": "호출 없음 → 이 글이 답"})
            messages.append({"role": "assistant", "content": out.text})
            return LoopResult(out.text.strip(), "answered", messages, n_llm, n_tool)

        # KANANA 형식은 한 번에 하나만 부르게 학습됐다. 여러 개가 나와도 첫 번째만 실행한다.
        call = parsed.calls[0] if parsed.calls else None
        emit({"type": "parsed", "node": "parser", "calls": parsed.calls, "errors": parsed.errors, "text": parsed.text,
              "verdict": ("호출 1개 실행" if len(parsed.calls) == 1 else
                          f"호출 {len(parsed.calls)}개 중 첫 번째만 실행" if parsed.calls else "호출 형식이 깨짐")})
        if call is None:
            name = parsed.errors[0].split(":", 1)[0]
            result = f"오류: 함수 호출 형식이 잘못되었습니다. {parsed.errors[0]}. 인자는 JSON 객체 한 줄로 쓰세요."
            emit({"type": "tool", "node": "tools", "name": name, "args": None, "content": result, "ok": False, "ms": 0})
            messages.append({"role": "assistant", "content": out.text.strip()})   # 깨진 글 그대로 남겨 무엇이 틀렸는지 보이게
            messages.append({"role": "tool", "content": result})
            continue
        else:
            name, args = call["name"], call["args"]
            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > max_same_call:
                return LoopResult("(같은 도구를 같은 인자로 계속 불러 멈췄습니다)", "repeated_call", messages, n_llm, n_tool)
            tool = by_name.get(name)
            t0 = time.perf_counter()
            denied = None
            if tool is not None and tool.writes:
                # 쓰기 도구: 코드가 멈추고 사람에게 묻는다. 모델은 이 단계를 건너뛸 수 없다.
                if approve is None:
                    denied = "이 비서에는 승인 절차가 없어 쓰기 도구를 실행하지 않습니다."
                else:
                    decision, new_args, note = approve(tool, args)
                    emit({"type": "approval", "node": "human", "tool": name, "args": args, "decision": decision,
                          "final_args": new_args, "note": note})
                    if decision != "approve":
                        denied = f"사용자가 실행을 거절했습니다.{(' 의견: ' + note) if note else ''}"
                    else:
                        args = new_args
            if tool is None:
                result, ok = f"오류: '{name}'라는 도구는 없습니다. 쓸 수 있는 도구: {', '.join(by_name)}", False
            elif denied:
                result, ok = denied, False
            else:
                try:
                    result, ok = tool.run(args), True
                except TypeError as e:          # 인자 이름·개수가 틀림
                    result, ok = f"오류: 인자가 맞지 않습니다({e}). 도구 설명의 parameters를 보고 다시 부르세요.", False
                except Exception as e:  # noqa: BLE001  — 실행 오류도 모델에게 돌려준다
                    result, ok = f"오류: 도구 실행 실패({type(e).__name__}: {e})", False
            n_tool += 1
            if len(result) > result_chars:
                result = result[:result_chars] + f"\n…(결과가 길어 {len(result) - result_chars}자를 잘랐습니다)"
            emit({"type": "tool", "node": "tools", "name": name, "args": args, "content": result, "ok": ok,
                  "ms": int((time.perf_counter() - t0) * 1000)})
        # 모델이 쓴 호출을 assistant 메시지로, 결과를 tool(템플릿에서 ipython 역할) 메시지로 대화에 붙인다
        messages.append({"role": "assistant", "content": parsed.text,
                         "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]})
        messages.append({"role": "tool", "content": result})
