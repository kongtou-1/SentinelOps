from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    app_name: str = "SentinelOps"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "ops_platform"
    postgres_user: str = "ops_platform"
    postgres_password: SecretStr
    jwt_secret: SecretStr
    jwt_issuer: str = "ops-platform"
    jwt_audience: str = "ops-platform-api"
    access_token_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    credential_key_file: Path = ROOT / ".local" / "credential.key"
    monitoring_token_file: Path = ROOT / ".local" / "monitoring.token"
    alertmanager_token_file: Path = ROOT / ".local" / "alertmanager.token"
    prometheus_url: str = "http://127.0.0.1:9090"
    alertmanager_url: str = "http://127.0.0.1:9093"
    notification_config_dir: Path = ROOT / ".local" / "notification-config"
    platform_workers: str = "operations,prediction,ai,source,report,askdata,execution"
    monitoring_rules_dir: Path = ROOT / ".local" / "monitoring" / "rules"
    monitoring_timeout_seconds: int = Field(default=10, ge=1, le=30)
    llm_probe_scheduler_enabled: bool = True
    service_probe_scheduler_enabled: bool = True
    blackbox_url: str = "http://127.0.0.1:9115"
    prediction_calculation_timeout_seconds: int = Field(default=120, ge=1, le=120)
    ai_enabled: bool = False
    inspection_sandbox_url: str = ""
    inspection_sandbox_token_file: Path = ROOT / ".local" / "inspection-sandbox.token"
    ai_provider_kind: Literal["model", "mock"] = "model"
    ai_base_url: str = "https://api.deepseek.com"
    ai_model: str = "deepseek-v4-flash"
    ai_key_file: Path = ROOT / ".local" / "ai-secrets" / "deepseek.key"
    ai_timeout_seconds: int = Field(default=180, ge=1, le=180)
    embedding_model_dir: Path = ROOT / ".local" / "embedding-model"
    source_store_dir: Path = ROOT / ".local" / "source-store"
    archive_dir: Path = ROOT / ".local" / "archives"
    source_input_dir: Path = ROOT / ".local" / "source-input"
    source_sync_timeout_seconds: int = Field(default=120, ge=5, le=600)
    source_max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    source_max_snapshot_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    source_max_files: int = Field(default=10000, ge=1, le=100000)
    source_max_cache_bytes: int = Field(default=1024 * 1024 * 1024, ge=1024)
    source_max_store_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1024)
    source_git_ca_file: Path | None = None
    source_svn_ca_file: Path | None = None
    report_font_file: Path = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
    smtp_address: str = "mailpit:1025"
    smtp_from: str = "alerts@example.test"
    smtp_require_tls: bool = False
    smtp_username: str = ""
    smtp_password_file: Path = ROOT / ".local" / "monitoring" / "smtp.password"

    @field_validator("smtp_from")
    @classmethod
    def validate_sender(cls, value):
        from email_validator import validate_email

        result = validate_email(value, check_deliverability=False, test_environment=True)
        if not result.ascii_email:
            raise ValueError("SMTP sender must be an ASCII email address")
        return result.ascii_email

    @field_validator("smtp_address")
    @classmethod
    def validate_smtp_address(cls, value):
        host, separator, port = value.rpartition(":")
        if not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("SMTP address must be host:port")
        return value

    @field_validator("postgres_password", "jwt_secret")
    @classmethod
    def require_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("Secrets must contain at least 32 characters")
        return value

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_url(cls, value):
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.query
            or url.fragment
        ):
            raise ValueError("AI URL must be HTTP(S) without credentials, query or fragment")
        return value.rstrip("/")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
