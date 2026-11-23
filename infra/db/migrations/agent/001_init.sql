-- 비서(agent) 스키마: 실행 기록. 모든 경계의 원문을 남긴다 → 블로그 그림 재료이자 5편 데이터.

CREATE TABLE runs (
  id         text PRIMARY KEY,                 -- 작업 id (패널의 job id)
  kind       text NOT NULL,                    -- assistant | chat_qa | s4_05 ...
  user_id    text,
  input      text NOT NULL DEFAULT '',
  model      text,
  status     text NOT NULL DEFAULT 'running',
  answer     text,
  stop_reason text,
  started_at timestamp NOT NULL DEFAULT localtimestamp,
  ended_at   timestamp
);

-- LLM 호출 한 번 = 한 행. 모델이 실제로 본 글자(prompt)와 쓴 글자(output) 그대로.
CREATE TABLE llm_calls (
  id            bigserial PRIMARY KEY,
  run_id        text NOT NULL REFERENCES runs ON DELETE CASCADE,
  seq           int  NOT NULL,
  prompt        text NOT NULL,
  output        text NOT NULL,
  prompt_tokens int,
  output_tokens int,
  ms            int,
  done_reason   text,
  first_token   jsonb,                         -- 첫 토큰 후보와 확률
  request       jsonb,                         -- HTTP 요청 본문(프롬프트 제외)
  created_at    timestamp NOT NULL DEFAULT localtimestamp
);
CREATE INDEX llm_calls_run ON llm_calls (run_id, seq);

-- 파서가 만든 호출과 실행 결과
CREATE TABLE tool_calls (
  id         bigserial PRIMARY KEY,
  run_id     text NOT NULL REFERENCES runs ON DELETE CASCADE,
  seq        int  NOT NULL,
  name       text NOT NULL,
  args       jsonb,
  result     text,
  ok         boolean NOT NULL,
  ms         int,
  created_at timestamp NOT NULL DEFAULT localtimestamp
);
CREATE INDEX tool_calls_run ON tool_calls (run_id, seq);
CREATE INDEX tool_calls_name ON tool_calls (name);
