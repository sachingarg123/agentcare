"""Application settings loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: agentcare/
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    """Central config — single source of truth for keys, paths, and toggles."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "PulseDesk"
    debug: bool = True
    secret_key: str = "dev-secret-change-me"

    # Database
    database_url: str = f"sqlite:///{DATA_DIR / 'agentcare.db'}"
    checkpoint_db_path: str = str(DATA_DIR / "checkpoints.db")

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24h

    # LLM — Groq primary, Google-hosted Gemma fallback (same as MediShield / deepagent)
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3-32b"
    google_api_key: str = ""
    google_model: str = "gemma-4-31b-it"
    # When true, pipeline enables LLM stage-2 safety + document classify (needs API keys).
    # Tests/CI should set USE_LLM=false for deterministic offline runs.
    use_llm: bool = True

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_project: str = "agentcare"
    langsmith_api_key: str = ""

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "PulseDesk <noreply@localhost>"
    smtp_tls: bool = True
    smtp_disabled: bool = False

    # Uploads
    upload_dir: str = str(DATA_DIR / "uploads")

    def ensure_data_dirs(self) -> None:
        """Create local data folders so SQLite / uploads don't fail on first run."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(self.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (safe to call from FastAPI Depends)."""
    return Settings()
