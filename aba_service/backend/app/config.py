import os
import time as _time
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "mysql+pymysql://labi_user:CHANGE_ME@localhost:3306/labi"
    jwt_secret: str = "insecure-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    seed_admin_username: str = "admin"
    seed_admin_password: str = ""
    # 배포 타임존 계약 — 아래 _pin_process_timezone / database.py 세션 tz 가 이 값을 쓴다.
    app_timezone: str = "Asia/Seoul"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _pin_process_timezone(tz: str) -> None:
    """배포 타임존 계약: 앱 프로세스를 이 tz 로 고정한다(호스트 OS tz 와 무관).

    aba_service 는 시각을 **naive-local** 로 저장/표시한다 — 저장은 `func.now()`(DB) ·
    `datetime.now()`/`datetime.fromtimestamp()`(앱), 표시는 프론트 `fmtTime` 이 ISO 문자열을
    그냥 slice 한다(tz 변환 없음). 즉 **프로세스 tz 가 곧 화면에 보이는 tz** 다. 그래서 UTC
    호스트에 배포하면 시각이 KST 보다 9시간 어긋난다. 여기서 프로세스 tz 를 못박아, 어느
    호스트에 올려도 화면이 KST(app_timezone)로 일관되게 나오도록 한다. (DB 쪽 짝은
    database.py 의 세션 `time_zone` 설정.)
    """
    os.environ["TZ"] = tz
    if hasattr(_time, "tzset"):
        _time.tzset()  # POSIX 전용 — 배포 대상(Linux)에서 동작


# 모듈 로드 시 1회 적용 — 어떤 datetime.now()/fromtimestamp() 호출보다 먼저 실행된다
# (config 가 database·모든 라우터보다 먼저 import 되므로).
_pin_process_timezone(get_settings().app_timezone)
