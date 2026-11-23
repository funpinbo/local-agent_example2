"""모델·서버 주소. 환경 변수로 바꾼다. 다른 파일에는 주소를 적지 않는다."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 모델 서빙 (Ollama)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "kanana-1.5-8b-q8")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "16384"))
# 출력 길이 상한. 없으면 구조화 출력(JSON 문법 제한)에서 생성이 끝나지 않는 경우가 있었다(s2/05 평가자, 25분 넘게 멈춤).
LLM_NUM_PREDICT = int(os.getenv("LLM_NUM_PREDICT", "2048"))

# 외부 시스템·백엔드
GROUPWARE_URL = os.getenv("GROUPWARE_URL", "http://127.0.0.1:8100")
MSDF_MODE = os.getenv("MSDF_MODE", "local")          # local | remote
MSDF_SCHEDULER = os.getenv("MSDF_SCHEDULER", "http://127.0.0.1:8787")

# 결과물
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT / "output"))
LOG_DIR = Path(os.getenv("LOG_DIR", ROOT / "blog" / "logs"))
SAMPLE_DIR = ROOT / "common" / "sample_data"
TEMPLATE_DIR = ROOT / "common" / "templates"
