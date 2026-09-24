from pathlib import Path

from jobapply.settings import Settings


def test_settings_have_local_safe_defaults(monkeypatch):
    for name in (
        "PROFILE_PATH",
        "HISTORY_PATH",
        "JEV_API_KEY",
        "JEV_MODEL",
        "JEV_MIN_CONFIDENCE",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "BROWSER_HEADLESS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.profile_path == Path("data/profile.yaml")
    assert settings.history_path == Path("data/history.sqlite3")
    assert settings.jev_min_confidence == 0.98
    assert settings.browser_headless is True


def test_settings_read_environment_overrides(monkeypatch):
    monkeypatch.setenv("PROFILE_PATH", "/tmp/person/profile.yaml")
    monkeypatch.setenv("HISTORY_PATH", "/tmp/person/history.db")
    monkeypatch.setenv("JEV_API_KEY", "jev-secret")
    monkeypatch.setenv("JEV_MODEL", "jev-test")
    monkeypatch.setenv("JEV_MIN_CONFIDENCE", "0.75")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("OPENAI_MODEL", "openai-test")
    monkeypatch.setenv("BROWSER_HEADLESS", "false")

    settings = Settings.from_env()

    assert settings.profile_path == Path("/tmp/person/profile.yaml")
    assert settings.history_path == Path("/tmp/person/history.db")
    assert settings.jev_api_key == "jev-secret"
    assert settings.jev_model == "jev-test"
    assert settings.jev_min_confidence == 0.75
    assert settings.openai_api_key == "openai-secret"
    assert settings.openai_model == "openai-test"
    assert settings.browser_headless is False


def test_settings_keep_missing_keys_and_models_optional(monkeypatch):
    for name in ("JEV_API_KEY", "JEV_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.jev_api_key is None
    assert settings.jev_model is None
    assert settings.openai_api_key is None
    assert settings.openai_model is None
