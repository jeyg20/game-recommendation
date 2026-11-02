from datetime import datetime, timedelta
from typing import cast

import pytest

from src import config, friends, steam_api, tools
from src.feedback import load_feedback, rate, save_feedback
from src.steam_api import OwnedGamesResponse
from src.storage import read_json, write_json
from src.tag_profile import CorpusRecord, DfTable

get_friend_ids = steam_api.get_friend_ids  # the real clients, before any test stubs them
get_player_names = steam_api.get_player_names

NAMES = {"101": "Alice", "102": "Bob", "103": "Carol", "104": "Dave"}
LIBRARIES: dict[str, list[int] | None] = {
    "101": [1, 2, 3, 9],
    "102": [1, 2],
    "103": None,  # private: Steam answers {"response": {}}
    "104": None,  # the request fails
}
GAME_NAMES = {1: "Tavern Tales", 2: "Gunpoint Run", 3: "Dread Manor", 9: "Owned Already"}
TAGS = {1: {"RPG": 10, "Co-op": 5}, 2: {"Shooter": 10}, 3: {"Horror": 10, "RPG": 5}, 9: {"RPG": 10}}


class FakeSteam:
    """get_friend_ids / get_player_names / get_owned_games stand-ins that count requests."""

    def __init__(self, ids: list[str] | None = None) -> None:
        self.ids = list(NAMES) if ids is None else ids
        self.calls = 0

    def friend_ids(self, steam_id: str, steam_api_key: str) -> list[str] | None:
        self.calls += 1
        return self.ids

    def names(self, steam_ids: list[str], steam_api_key: str) -> dict[str, str]:
        self.calls += 1
        return {i: NAMES[i] for i in steam_ids}

    def owned(self, steam_id: str, steam_api_key: str) -> OwnedGamesResponse | None:
        self.calls += 1
        if steam_id == "104":
            return None
        appids = LIBRARIES[steam_id]
        if appids is None:
            return {"response": {}}
        games = [{"appid": a, "name": GAME_NAMES[a]} for a in appids]
        return cast(OwnedGamesResponse, {"response": {"game_count": len(games), "games": games}})


@pytest.fixture
def steam(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> FakeSteam:
    """Four friends (one private, one failing), corpus tags from TAGS, an RPG-heavy profile, game 9 owned."""
    fake = FakeSteam()
    monkeypatch.setattr(steam_api, "get_friend_ids", fake.friend_ids)
    monkeypatch.setattr(steam_api, "get_player_names", fake.names)
    monkeypatch.setattr(steam_api, "get_owned_games", fake.owned)
    monkeypatch.setattr(friends.time, "sleep", lambda seconds: None)

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {a: {"appid": a, "genre": "", "tags": TAGS[a], "fetched_at": "", "status": "ok"} for a in appids}

    monkeypatch.setattr(tools, "enrich_tags", enrich_tags)
    owned = cast(list[object], [{"appid": 9}])
    tools.state |= {"player_games": owned, "games": {}, "df_table": df_table, "profile": {"RPG": 1.0, "Co-op": 0.5}}
    return fake


def picks(
    min_friends: int = 1, limit: int = 10, boost_tags: list[str] | None = None, avoid_tags: list[str] | None = None
) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], tools.friends_games(min_friends, limit, boost_tags, avoid_tags)["picks"])


def test_private_and_failed_libraries_are_skipped_and_counted(steam: FakeSteam) -> None:
    result = tools.friends_games()
    assert result["friends"] == 4
    assert result["private_libraries"] == 2
    ranked = cast(list[dict[str, object]], result["picks"])
    assert ranked[0]["name"] == "Tavern Tales"
    assert {p["name"]: p["owned_by"] for p in ranked} == {
        "Tavern Tales": ["Alice", "Bob"],
        "Gunpoint Run": ["Alice", "Bob"],
        "Dread Manor": ["Alice"],
    }


def test_games_the_player_owns_or_dismissed_never_come_back(steam: FakeSteam) -> None:
    assert "Owned Already" not in [p["name"] for p in picks()]
    feedback = load_feedback()
    rate(feedback, "not_interested", 2, GAME_NAMES[2], {"Shooter": 10})
    save_feedback(feedback)
    assert "Gunpoint Run" not in [p["name"] for p in picks()]


def test_min_friends_limit_and_tag_rules(steam: FakeSteam) -> None:
    assert [p["name"] for p in picks(min_friends=2)] == ["Tavern Tales", "Gunpoint Run"]
    assert len(picks(limit=1)) == 1
    assert [p["name"] for p in picks(boost_tags=["co-op"])] == ["Tavern Tales"]
    assert "Dread Manor" not in [p["name"] for p in picks(avoid_tags=["Horror"])]
    assert tools.friends_games(boost_tags=["Coop-ish"])["unknown_tags_ignored"]


def test_second_call_within_a_day_makes_no_steam_request(steam: FakeSteam) -> None:
    first = tools.friends_games()
    calls = steam.calls
    assert calls == 6  # friends list, names, one library per friend
    assert tools.friends_games() == first
    assert steam.calls == calls


def test_day_old_cache_is_refetched(steam: FakeSteam) -> None:
    old = (datetime.now() - timedelta(days=1, minutes=1)).isoformat()
    write_json(config.FRIENDS_PATH, {"fetched_at": old, "friends": {}, "game_names": {}})
    assert tools.friends_games()["friends"] == 4
    assert steam.calls == 6
    cache = cast(dict[str, object], read_json(config.FRIENDS_PATH))
    assert cache["fetched_at"] != old


def test_unreadable_friends_list_is_an_error_and_not_cached(steam: FakeSteam, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_api, "get_friend_ids", lambda steam_id, steam_api_key: None)
    assert "friends list" in cast(str, tools.friends_games()["error"])
    assert not config.FRIENDS_PATH.exists()


def test_status_messages_never_name_a_friend(steam: FakeSteam, monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(friends.status, "update", lambda label=None, done=None, total=None: said.append(str(label)))
    monkeypatch.setattr(friends.status, "say", said.append)
    tools.friends_games()
    assert said
    assert not any(secret in line for line in said for secret in [*NAMES, *NAMES.values()])


def test_get_friend_ids_parses_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[object] = [
        {"friendslist": {"friends": [{"steamid": "101", "relationship": "friend", "friend_since": 0}]}},
        {},  # private friends list (Steam answers 401, which steam_request turns into None, but be safe)
        None,
    ]
    monkeypatch.setattr(steam_api, "steam_request", lambda url, params: answers.pop(0))
    assert get_friend_ids("1", "k") == ["101"]
    assert get_friend_ids("1", "k") is None
    assert get_friend_ids("1", "k") is None


def test_get_player_names_batches_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def summary(steam_ids: str, steam_api_key: str) -> dict[str, object] | None:
        sent.append(steam_ids)
        players = [{"steamid": i, "personaname": f"name {i}"} for i in steam_ids.split(",")]
        return {"response": {"players": players}} if len(sent) == 1 else None

    monkeypatch.setattr(steam_api, "get_player_sumamry", summary)
    ids = [str(i) for i in range(150)]
    names = get_player_names(ids, "k")
    assert [len(batch.split(",")) for batch in sent] == [100, 50]
    assert names == {str(i): f"name {i}" for i in range(100)}  # the failed second batch is left out
