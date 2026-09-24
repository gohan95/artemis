"""Fingerprint logic -- no real browser is launched."""

from artemis.browser import Fingerprint, ProxyConfig


def test_fingerprint_is_stable_for_the_same_profile_dir(tmp_path):
    profile_dir = tmp_path / "chromium"

    first = Fingerprint.for_profile(profile_dir)
    second = Fingerprint.for_profile(profile_dir)

    assert first == second


def test_fingerprint_differs_across_profile_dirs(tmp_path):
    # Small fingerprint space, so sample several paths instead of comparing
    # just two (which can coincidentally collide).
    baseline = Fingerprint.for_profile(tmp_path / "profile-0")
    others = [Fingerprint.for_profile(tmp_path / f"profile-{i}") for i in range(1, 10)]

    assert any(other != baseline for other in others)


def test_accept_language_agrees_with_locale(tmp_path):
    fingerprint = Fingerprint.for_profile(tmp_path / "chromium")

    assert fingerprint.accept_language().startswith(fingerprint.locale)


def test_proxy_config_omits_unset_credentials():
    proxy = ProxyConfig(server="http://proxy.example:8080")

    assert proxy.to_playwright() == {"server": "http://proxy.example:8080"}


def test_proxy_config_includes_credentials_when_set():
    proxy = ProxyConfig(server="http://proxy.example:8080", username="u", password="p")

    assert proxy.to_playwright() == {
        "server": "http://proxy.example:8080",
        "username": "u",
        "password": "p",
    }
