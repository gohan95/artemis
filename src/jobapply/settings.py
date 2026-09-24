"""Environment-backed settings for the job application CLI."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables on demand."""

    profile_path: Path = Path("data/profile.yaml")
    history_path: Path = Path("data/history.sqlite3")
    jev_api_key: str | None = None
    jev_model: str | None = None
    jev_min_confidence: float = 0.98
    openai_api_key: str | None = None
    openai_model: str | None = None
    browser_headless: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from process environment variables."""
        return cls()
