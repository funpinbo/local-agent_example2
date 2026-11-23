"""테스트용 가짜 LLM. 정해 둔 응답을 차례로 돌려준다.

그래프의 연결과 흐름만 검증한다(GPU 없이). 글에 넣는 로그는 실제 모델에서만 받는다.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


def tool_call(name: str, args: dict | None = None, content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=[
        {"name": name, "args": args or {}, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"}])


Script = AIMessage | str | Callable[[list[BaseMessage]], AIMessage | str]


class ScriptedChatModel(BaseChatModel):
    """responses를 순서대로 돌려준다. 다 쓰면 예외.

    - AIMessage / str: 그대로 돌려준다 (매번 새 객체로 복사: add_messages가 id로 덮어쓰지 않게)
    - 함수: 받은 메시지로 응답을 만든다
    structured: with_structured_output()이 돌려줄 객체 목록
    """

    responses: list[Any]
    structured: list[Any] = []
    calls: list[list[BaseMessage]] = []
    index: int = 0
    s_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if self.index >= len(self.responses):
            raise AssertionError(f"가짜 LLM 응답이 모자랍니다 ({self.index + 1}번째 호출)")
        item = self.responses[self.index]
        self.index += 1
        if callable(item) and not isinstance(item, BaseMessage):
            item = item(messages)
        if isinstance(item, str):
            return AIMessage(content=item, id=f"fake-{uuid.uuid4().hex[:8]}")
        return item.model_copy(update={"id": f"fake-{uuid.uuid4().hex[:8]}"}, deep=True)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any):
        def run(_input: Any) -> Any:
            if self.s_index >= len(self.structured):
                raise AssertionError("가짜 구조화 출력이 모자랍니다")
            item = self.structured[self.s_index]
            self.s_index += 1
            if isinstance(item, Exception):
                raise item
            return schema(**item) if isinstance(item, dict) and isinstance(schema, type) else item
        return RunnableLambda(run)
