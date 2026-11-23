-- 그룹웨어 스키마. search_path가 대상 스키마로 잡힌 상태에서 돈다(테스트는 임시 스키마).
-- SQLite 시절의 JSON 문자열 칸(recipients, cc, attendees, line, history)을 행으로 풀었다.

CREATE TABLE users (
  id    text PRIMARY KEY,
  name  text NOT NULL,
  team  text NOT NULL,
  title text NOT NULL
);

CREATE TABLE mails (
  id      bigserial PRIMARY KEY,
  sender  text NOT NULL REFERENCES users,
  subject text NOT NULL,
  body    text NOT NULL DEFAULT '',
  sent_at timestamp NOT NULL
);
CREATE INDEX mails_sent_at ON mails (sent_at);

CREATE TABLE mail_recipients (
  mail_id bigint NOT NULL REFERENCES mails ON DELETE CASCADE,
  user_id text   NOT NULL REFERENCES users,
  kind    text   NOT NULL CHECK (kind IN ('to', 'cc')),
  seq     int    NOT NULL,
  read_at timestamp,
  PRIMARY KEY (mail_id, user_id, kind)
);
CREATE INDEX mail_recipients_user ON mail_recipients (user_id);

CREATE TABLE events (
  id        bigserial PRIMARY KEY,
  organizer text NOT NULL REFERENCES users,
  title     text NOT NULL,
  start_at  timestamp NOT NULL,
  end_at    timestamp NOT NULL,
  location  text NOT NULL DEFAULT '',
  memo      text NOT NULL DEFAULT ''
);
CREATE INDEX events_start ON events (start_at);

CREATE TABLE event_attendees (
  event_id bigint NOT NULL REFERENCES events ON DELETE CASCADE,
  user_id  text   NOT NULL REFERENCES users,
  seq      int    NOT NULL,
  PRIMARY KEY (event_id, user_id)
);

CREATE TABLE approvals (
  id         bigserial PRIMARY KEY,
  drafter    text NOT NULL REFERENCES users,
  kind       text NOT NULL,
  title      text NOT NULL,
  body       text NOT NULL DEFAULT '',
  status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  step       int  NOT NULL DEFAULT 0,          -- 지금 차례인 결재선 칸(0부터)
  created_at timestamp NOT NULL,
  decided_at timestamp
);
CREATE INDEX approvals_created ON approvals (created_at);

-- 결재선 한 칸 = 한 행. 결정이 채워진 행이 곧 결재 이력이다.
CREATE TABLE approval_lines (
  approval_id bigint NOT NULL REFERENCES approvals ON DELETE CASCADE,
  seq         int    NOT NULL,
  approver    text   NOT NULL REFERENCES users,
  decision    text CHECK (decision IN ('approve', 'reject')),
  comment     text NOT NULL DEFAULT '',
  decided_at  timestamp,
  PRIMARY KEY (approval_id, seq)
);
CREATE INDEX approval_lines_approver ON approval_lines (approver);

CREATE TABLE approval_attachments (
  approval_id bigint PRIMARY KEY REFERENCES approvals ON DELETE CASCADE,
  name        text  NOT NULL,
  content     bytea NOT NULL
);

CREATE TABLE webhooks (
  id  bigserial PRIMARY KEY,
  url text NOT NULL UNIQUE
);

-- 변경 이벤트(outbox). 상태를 바꾸는 트랜잭션 안에서 한 행 넣고 NOTIFY 한다.
-- 듣는 쪽은 NOTIFY로 id를 받고, 놓친 것은 id 순서로 다시 읽는다.
CREATE TABLE outbox_events (
  id         bigserial PRIMARY KEY,
  kind       text   NOT NULL,                  -- mails | events | approvals
  ref_id     bigint,
  users      text[] NOT NULL,
  payload    jsonb  NOT NULL DEFAULT '{}',
  created_at timestamp NOT NULL DEFAULT localtimestamp
);
