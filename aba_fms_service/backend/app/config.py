import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# backend/.env 를 로드한다. PM2 ecosystem 이나 shell env 가 이미 설정한 값은
# override=False 로 보존한다(운영 환경 변수 우선).
load_dotenv(BASE_DIR / ".env", override=False)

# MariaDB (labi) — 예전 작업에서 만든 rc_ 접두사 테이블을 그대로 사용한다.
# 관리자/로봇 데이터 모두 동일한 labi 데이터베이스에 저장되며, 아래 두 URL은
# 필요 시 물리적으로 분리할 수 있도록 이름만 구분해 둔다 (기본값은 동일 DB).
_DEFAULT_DB_URL = "mysql+aiomysql://labi_user:106a1752c19b1f58429b7a6c131dfedb@127.0.0.1:3306/labi"

DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_DB_URL)
ADMIN_DATABASE_URL = os.getenv("ADMIN_DATABASE_URL", DATABASE_URL)
ROBOT_DATABASE_URL = os.getenv("ROBOT_DATABASE_URL", DATABASE_URL)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

# OpenAI Realtime (음성 대화 STT/TTS + function calling) — voice.py 가 사용.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_REALTIME_VOICE = os.getenv("OPENAI_REALTIME_VOICE", "alloy")
