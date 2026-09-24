"""Local configuration for where profile, history, and learned-answer data live."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from artemis.pacing import PacingProfile


class Settings(BaseSettings):
    """Environment-backed paths and run behavior, all overridable for tests."""

    model_config = SettingsConfigDict(env_prefix="ARTEMIS_")

    profile_path: Path = Path("data/profile.yaml")
    history_path: Path = Path("data/history.sqlite3")
    learned_answers_path: Path = Path("data/learned_answers.yaml")
    receipts_dir: Path = Path("data/receipts")
    browser_headless: bool = True
    browser_profile_dir: Path = Path("browser-state/chromium")
    browser_channel: str | None = "chrome"

    proxy_server: str | None = None
    proxy_username: str | None = None
    proxy_password: SecretStr | None = None

    pacing_enabled: bool = True
    pacing_seed: int | None = None
    pacing_key_delay_min_ms: float = 55.0
    pacing_key_delay_max_ms: float = 165.0
    pacing_think_min_ms: float = 400.0
    pacing_think_max_ms: float = 1800.0

    def proxy(self) -> dict[str, str] | None:
        if not self.proxy_server:
            return None
        config: dict[str, str] = {"server": self.proxy_server}
        if self.proxy_username:
            config["username"] = self.proxy_username
            config["password"] = (
                self.proxy_password.get_secret_value() if self.proxy_password else ""
            )
        return config

    def pacing_profile(self) -> PacingProfile:
        return PacingProfile(
            key_delay_ms=(self.pacing_key_delay_min_ms, self.pacing_key_delay_max_ms),
            think_ms=(self.pacing_think_min_ms, self.pacing_think_max_ms),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()
