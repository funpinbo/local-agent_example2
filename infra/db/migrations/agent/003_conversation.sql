-- 대화 이어가기: 같은 대화의 실행끼리 묶는다.
ALTER TABLE runs ADD COLUMN conversation_id text;
CREATE INDEX runs_conversation ON runs (conversation_id, started_at);
