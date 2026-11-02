"""steam_api's thin HTTP client."""

import pytest
import requests

from src import steam_api


def test_a_failed_request_never_prints_the_key_or_steam_id(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(steam_api.status, "say", said.append)

    def fail(url: str, params: dict[str, object], timeout: int) -> None:
        query = "&".join(f"{name}={value}" for name, value in params.items())
        raise requests.HTTPError(f"400 Client Error: Bad Request for url: {url}?{query}")

    monkeypatch.setattr(requests, "get", fail)
    assert steam_api.get_player_achievements("76561190000000000", "SECRETKEY123", 440) is None
    assert said, "the failure should still be reported"
    assert "SECRETKEY123" not in said[0] and "76561190000000000" not in said[0]
    assert "<key>" in said[0] and "appid=440" in said[0]


def test_a_failed_summary_request_never_prints_friends_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(steam_api.status, "say", said.append)

    def fail(url: str, params: dict[str, object], timeout: int) -> None:
        raise requests.HTTPError(f"500 Server Error for url: {url}?steamids={params['steamids']}")

    monkeypatch.setattr(requests, "get", fail)
    assert steam_api.get_player_names(["76561190000000001", "76561190000000002"], "SECRETKEY123") == {}
    assert said and "76561190000000001" not in said[0] and "<steamids>" in said[0]
