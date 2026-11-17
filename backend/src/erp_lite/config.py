"""应用配置：全部从环境变量读取。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 数据库连接，例如 postgresql+psycopg://erp:erp@db:5432/erp
    database_url: str = "sqlite:///./erp.db"

    # JWT 签名密钥
    jwt_secret: str = "change-me"

    # 单管理员账号（默认仅限本地）
    erp_admin_user: str = "admin"
    erp_admin_password: str = "admin123"

    # JWT 有效期（分钟）
    jwt_expire_minutes: int = 60 * 12


settings = Settings()
