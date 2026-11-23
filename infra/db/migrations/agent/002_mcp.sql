-- MCP JSON-RPC 메시지 원문 (4편 2장). 실행 밖(서버 연결 때) 메시지는 run_id가 비어 있다.
CREATE TABLE mcp_messages (
  id         bigserial PRIMARY KEY,
  run_id     text REFERENCES runs ON DELETE CASCADE,
  server     text NOT NULL,
  direction  text NOT NULL CHECK (direction IN ('send', 'recv')),
  method     text,
  rpc_id     text,
  msg        jsonb NOT NULL,
  bytes      int,
  created_at timestamp NOT NULL DEFAULT localtimestamp
);
CREATE INDEX mcp_messages_run ON mcp_messages (run_id, id);
