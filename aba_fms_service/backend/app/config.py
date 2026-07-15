import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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
