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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
