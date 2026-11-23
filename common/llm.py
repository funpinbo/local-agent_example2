"""get_llm() — Ollama로 띄운 KANANA를 LangChain 채팅 모델로.

KANANA는 도구를 부를 때 본문에 `<function=이름>{JSON}</function>`을 쓰고,
Ollama는 이것을 tool_calls로 바꿔 주지 못한다(tools/check_ollama.py로 확인할 수 있다).
그래서 ChatOllama의 응답을 한 번 거쳐 본문의 호출을 AIMessage.tool_calls로 옮긴다.
그러면 LangGraph의 ToolNode, tools_condition을 그대로 쓸 수 있다.

- 형식은 맞는데 JSON이 깨진 호출은 invalid_tool_calls에 넣는다 (04_retry_fallback_stop이 이것을 본다).
- vLLM이라면 --tool-call-parser가 서버에서 하던 일이다.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_ollama import ChatOllama

from common import config
from common.kanana_tools import parse_function_calls


def _move_calls(result: ChatResult) -> ChatResult:
    for gen in result.generations:
        msg = gen.message
        if not isinstance(msg, AIMessage) or msg.tool_calls:
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if "<function=" not in content:
            continue
        parsed = parse_function_calls(content)
        tool_calls = [
            {"name": c["name"], "args": c["args"], "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
            for c in parsed.calls
        ]
        invalid = [
            {"name": e.split(":", 1)[0], "args": None, "id": f"call_{uuid.uuid4().hex[:12]}",
             "error": e, "type": "invalid_tool_call"}
            for e in parsed.errors
        ]
        gen.message = AIMessage(
            content=parsed.text,
            tool_calls=tool_calls,
            invalid_tool_calls=invalid,
            usage_metadata=msg.usage_metadata,
            additional_kwargs=msg.additional_kwargs,
            response_metadata={**msg.response_metadata, "raw_content": content},
            id=msg.id,
        )
    return result


class KananaChatOllama(ChatOllama):
    """ChatOllama + KANANA 도구 호출 해석."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return _move_calls(super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs))

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return _move_calls(await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs))


def get_llm(**overrides: Any) -> KananaChatOllama:
    opts = dict(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=config.LLM_TEMPERATURE,
        num_ctx=config.LLM_NUM_CTX,
        num_predict=config.LLM_NUM_PREDICT,
    )
    opts.update(overrides)
    return KananaChatOllama(**opts)
