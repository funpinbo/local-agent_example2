-- 컨테이너를 처음 만들 때 한 번만 돈다.
-- 서비스마다 DB 계정을 나눈다: 그룹웨어 데이터는 groupware 계정만 만진다.
-- 비서(assistant)는 그룹웨어 스키마를 직접 읽을 수 없다 → 반드시 그룹웨어 API(MCP)를 거친다.

CREATE ROLE groupware LOGIN PASSWORD 'groupware';
CREATE ROLE assistant LOGIN PASSWORD 'assistant';

GRANT CREATE ON DATABASE office TO groupware, assistant;   -- 자기 스키마(테스트용 임시 스키마 포함)를 만든다

CREATE SCHEMA groupware AUTHORIZATION groupware;
CREATE SCHEMA agent AUTHORIZATION assistant;
CREATE SCHEMA docs AUTHORIZATION assistant;

CREATE EXTENSION IF NOT EXISTS vector;                       -- 5편 임베딩
