from pathlib import Path

import pytest

from src import config, library, steam_api, storage
from src.steam_api import OwnedGame


def owned(appid: int, has_stats: bool) -> OwnedGame:
    game: OwnedGame = {
        "appid": appid,
        "name": f"Game {appid}",
        "playtime_forever": 600,
        "img_icon_url": "",
        "playtime_windows_forever": 600,
        "playtime_mac_forever": 0,
        "playtime_linux_forever": 0,
        "playtime_deck_forever": 0,
        "rtime_last_played": 0,
        "playtime_disconnected": 0,
    }
    if has_stats:
        game["has_community_visible_stats"] = True
    return game


@pytest.fixture
def requests_made(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Fake achievements: appid 1 has 3 of 4, appid 3 has none. Records each requested appid; no pacing sleep."""
    made: list[int] = []
    answers = {1: (3, 4), 3: None}

    def fake(steam_id: str, key: str, appid: int) -> tuple[int, int] | None:
        made.append(appid)
        return answers[appid]

    monkeypatch.setattr(steam_api, "get_player_achievements", fake)
    monkeypatch.setattr(library.time, "sleep", lambda seconds: None)
    return made


def test_achievements_are_fetched_only_for_games_with_visible_stats(requests_made: list[int]) -> None:
    achievements = library.fetch_achievements([owned(1, True), owned(2, False), owned(3, True)])
    assert requests_made == [1, 3]
    assert achievements == {1: (3, 4), 3: None}


def test_achievement_shares_skip_games_without_achievements_and_take_str_keys() -> None:
    assert library.achievement_shares({"1": [3, 4], "2": None, "3": [0, 0]}) == {1: 0.75}  # pyright: ignore[reportArgumentType]


def test_achievement_shares_load_from_cache_without_requests_on_the_second_run(
    requests_made: list[int], isolated_data: Path
) -> None:
    games = [owned(1, True), owned(3, True)]
    assert library.load_achievement_shares(games) == {1: 0.75}
    assert storage.read_json(config.ACHIEVEMENTS_PATH) == {"1": [3, 4], "3": None}
    requests_made.clear()
    assert library.load_achievement_shares(games) == {1: 0.75}
    assert requests_made == []


def test_get_player_achievements_counts_unlocked(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"playerstats": {"achievements": [{"achieved": 1}, {"achieved": 0}, {"achieved": 1}], "success": True}}
    monkeypatch.setattr(steam_api, "steam_request", lambda url, params: payload)
    assert steam_api.get_player_achievements("id", "key", 1) == (2, 3)


def test_get_player_achievements_is_none_without_achievements_or_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_api, "steam_request", lambda url, params: {"playerstats": {"success": True}})
    assert steam_api.get_player_achievements("id", "key", 1) is None
    monkeypatch.setattr(steam_api, "steam_request", lambda url, params: None)
    assert steam_api.get_player_achievements("id", "key", 1) is None
