# Tool Calling 층

`chat_ui/agent/toolcall.py`. 도구 호출 한 번이 모양을 바꾸는 네 단계를 코드에서 나눠 모두 드러낸다.

```
① 도구 정의 (JSON Schema)                 Tool.schema()
   ↓ 원본 chat template (jinja2)            render()
② 모델이 보는 글자 (시스템 블록 안 도구 설명)
   ↓ Ollama /api/generate, raw=true         RawKanana.generate()
③ 모델이 쓰는 글자   <function=이름>{…}</function>
   ↓ 파서 → 실행 → 결과를 tool(ipython) 메시지로 붙여 다시 ②   run_loop()
```

## 왜 직접 렌더링하나

`/api/chat`에 메시지와 도구를 넘기면 Ollama가 Modelfile의 Go 템플릿으로 프롬프트를 만들고, 우리는 그 글자를 볼 수 없다. 원본 템플릿(`serving/kanana_chat_template.jinja`)을 `trim_blocks=True, lstrip_blocks=True`(transformers와 같은 설정)로 직접 렌더링해 raw 모드로 보내면, 모델이 본 글자와 쓴 글자를 그대로 남길 수 있다.

- BOS는 Ollama가 붙이므로 렌더링할 때 비운다(넣으면 BOS가 두 번).
- 생성을 멈춘 토큰(`<|eom_id|>`/`<|eot_id|>`)은 응답에 오지 않고 `done_reason`만 온다.
- `logprobs: true, top_logprobs: 5`로 첫 토큰의 후보와 확률을 받는다. 고른 토큰을 먼저(`chosen`), 후보를 뒤에 남긴다(Ollama가 돌려주는 후보 글자가 고른 토큰과 모양이 다를 때가 있다).

## 도구

```python
Tool(name, description, parameters, fn, writes=False, source="local")
from_langchain(lc_tool)     # 3편 @tool 함수 → Tool (convert_to_openai_tool)
```

`source`: `local`(비서 코드 안), `mcp:<서버>`, `skill`(Skill 로더). `writes=True`면 사람 승인 대상.

## 실행 루프 `run_loop(llm, messages, tools, *, emit, approve, max_llm_calls=8, max_same_call=2)`

- 모델이 호출 없이 쓰면 그 글이 답이다.
- 호출이 여러 개면 첫 번째만 실행한다(KANANA는 한 번에 하나만 부르도록 학습됐다).
- **멈추지 않고 돌려주는 실패**: 없는 도구, 깨진 JSON, 인자 오류, 실행 오류 → 오류 글을 도구 결과로 붙여 모델이 고치게 한다.
- **코드가 멈추는 조건**: LLM 호출 상한, 같은 도구·같은 인자 세 번째.
- **쓰기 도구**: `approve(tool, args) -> (decision, args, note)`. 승인하면(고친 인자로) 실행, 거절하면 "사용자가 실행을 거절했습니다. 의견: …"을 결과로. `approve`가 없으면 쓰기 도구는 실행하지 않는다.
- `tools`가 함수면 LLM을 부를 때마다 다시 읽는다(Skill을 불러오면 도구가 바뀐다).
- 도구 결과는 6,000자에서 자른다.

두 번째 프롬프트 = 첫 프롬프트 + 모델이 쓴 글자 그대로 + `<|eom_id|>` + `ipython` 결과 + 다음 `assistant` 머리. `llm_raw` 이벤트의 `prompt_delta`가 이 "이번에 붙은 글자"다(도구 목록이 바뀌어 앞부분이 달라지면 `None`).

## 테스트용 모델

`ScriptedRaw([...])` — 정해 둔 글자(또는 프롬프트를 받아 글자를 만드는 함수)를 차례로 돌려준다. 받은 프롬프트는 `.prompts`에 남는다.

## 실습 스크립트

```powershell
.\.venv\Scripts\python.exe steps\p1_tool_calling\01_prompt_anatomy.py   # 도구 0→11개일 때 프롬프트 크기, 원본 템플릿 대 Ollama 템플릿
.\.venv\Scripts\python.exe steps\p1_tool_calling\02_first_token.py      # 질문별 첫 토큰과 확률 (부를지 말지)
```
