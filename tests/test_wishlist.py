from datetime import datetime, timedelta
from typing import cast

import pytest

from src import config, steam_api, tools, wishlist
from src import recommend as rec
from src.steam_api import StoreApp, WishlistItem
from src.steamspy import GameSummary
from src.storage import read_json, write_json
from src.tag_profile import CorpusRecord, DfTable

get_wishlist = steam_api.get_wishlist  # the real client, taken before conftest's no_wishlist stubs it

ITEMS: list[WishlistItem] = [
    {"appid": 1, "priority": 2, "date_added": 1700000000},
    {"appid": 2, "priority": 1, "date_added": 1700000000},
    {"appid": 3, "priority": 0, "date_added": 1700000000},  # no usable tags
]
TAGS = {1: {"RPG": 10}, 2: {"Shooter": 10}}  # the corpus records enrich_tags returns
NAMES = {1: "Tavern Tales", 2: "Gunpoint Run", 3: "Soundtrack"}


def store_page(game_id: str) -> dict[str, StoreApp] | None:
    """steam_api.get_game_details stand-in: a store page with the name from NAMES."""
    return {game_id: {"success": True, "data": {"name": NAMES[int(game_id)]}}}


class FakeSteam:
    """get_wishlist stand-in that counts requests."""

    def __init__(self, items: list[WishlistItem] | None) -> None:
        self.items = items
        self.calls = 0

    def __call__(self, steam_id: str, steam_api_key: str = "") -> list[WishlistItem] | None:
        self.calls += 1
        return self.items


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> None:
    """Corpus records for TAGS, store names from NAMES, an RPG-only profile."""

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {
            a: {"appid": a, "genre": "", "tags": TAGS[a], "fetched_at": "", "status": "ok"} for a in appids if a in TAGS
        }

    monkeypatch.setattr(tools, "enrich_tags", enrich_tags)
    monkeypatch.setattr(steam_api, "get_game_details", store_page)
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": {"RPG": 1.0}}


def test_get_wishlist_parses_items_empty_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[object] = [{"response": {"items": ITEMS}}, {"response": {}}, None]
    monkeypatch.setattr(steam_api, "steam_request", lambda url, params: answers.pop(0))
    assert get_wishlist("1") == ITEMS
    assert get_wishlist("1") == []  # empty or private
    assert get_wishlist("1") is None  # request failed


def test_second_read_within_a_day_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    steam = FakeSteam(ITEMS)
    monkeypatch.setattr(steam_api, "get_wishlist", steam)
    assert wishlist.load_wishlist() == ITEMS
    assert wishlist.load_wishlist() == ITEMS
    assert steam.calls == 1


def test_day_old_cache_is_refetched_and_keeps_names(monkeypatch: pytest.MonkeyPatch) -> None:
    old = (datetime.now() - timedelta(days=1, minutes=1)).isoformat()
    write_json(config.WISHLIST_PATH, {"fetched_at": old, "items": ITEMS[:1], "names": {"1": "Tavern Tales"}})
    steam = FakeSteam(ITEMS)
    monkeypatch.setattr(steam_api, "get_wishlist", steam)
    assert wishlist.load_wishlist() == ITEMS
    assert steam.calls == 1
    cache = cast(dict[str, object], read_json(config.WISHLIST_PATH))
    assert cache["names"] == {"1": "Tavern Tales"}


@pytest.mark.parametrize("items", [None, []])
def test_unreadable_wishlist_is_not_cached(monkeypatch: pytest.MonkeyPatch, items: list[WishlistItem] | None) -> None:
    steam = FakeSteam(items)
    monkeypatch.setattr(steam_api, "get_wishlist", steam)
    assert wishlist.load_wishlist() == items
    assert wishlist.load_wishlist() == items
    assert steam.calls == 2
    assert not config.WISHLIST_PATH.exists()


def test_rank_wishlist_best_fit_first_with_priority(monkeypatch: pytest.MonkeyPatch, fake_store: None) -> None:
    monkeypatch.setattr(steam_api, "get_wishlist", FakeSteam(ITEMS))
    result = tools.rank_wishlist()
    ranked = cast(list[dict[str, object]], result["ranked"])
    assert [(r["name"], r["priority"]) for r in ranked] == [("Tavern Tales", 2), ("Gunpoint Run", 1)]
    assert cast(float, ranked[0]["score"]) > cast(float, ranked[1]["score"])
    assert result["wishlist_size"] == 3
    assert cast(dict[str, object], result["not_ranked"])["games"] == ["Soundtrack"]
    assert len(cast(list[object], tools.rank_wishlist(limit=1)["ranked"])) == 1


def test_rank_wishlist_names_are_looked_up_once(monkeypatch: pytest.MonkeyPatch, fake_store: None) -> None:
    monkeypatch.setattr(steam_api, "get_wishlist", FakeSteam(ITEMS))
    tools.rank_wishlist()
    monkeypatch.setattr(steam_api, "get_game_details", lambda game_id: pytest.fail("name not cached"))
    assert cast(list[object], tools.rank_wishlist()["ranked"])


def test_store_name_skips_missing_and_blank_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: dict[str, StoreApp] = {"1": {"success": True, "data": {"name": ""}}, "2": {"success": False}}
    monkeypatch.setattr(steam_api, "get_game_details", lambda game_id: {game_id: pages[game_id]})
    assert wishlist.store_name(1) is None
    assert wishlist.store_name(2) is None
    monkeypatch.setattr(steam_api, "get_game_details", lambda game_id: None)
    assert wishlist.store_name(3) is None


@pytest.mark.parametrize(("items", "says"), [(None, "request to Steam failed"), ([], "empty or private")])
def test_rank_wishlist_says_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, fake_store: None, items: list[WishlistItem] | None, says: str
) -> None:
    monkeypatch.setattr(steam_api, "get_wishlist", FakeSteam(items))
    assert says in cast(str, tools.rank_wishlist()["error"])


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> None:
    """One RPG pool holding games 1 and 2, both tagged RPG."""

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        pool = {
            str(a): cast(GameSummary, {"appid": a, "name": NAMES[a], "positive": 900, "negative": 100}) for a in (1, 2)
        }
        return {"RPG": pool}, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {a: {"appid": a, "genre": "", "tags": {"RPG": 10}, "fetched_at": "", "status": "ok"} for a in appids}

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": {"RPG": 1.0}}


def on_wishlist() -> dict[object, object]:
    picks = cast(list[dict[str, object]], tools.recommend_games(include_backlog=False)["recommendations"])
    return {p["appid"]: p["on_wishlist"] for p in picks}


def test_picks_say_whether_they_are_wishlisted(monkeypatch: pytest.MonkeyPatch, pools: None) -> None:
    monkeypatch.setattr(steam_api, "get_wishlist", FakeSteam(ITEMS[:1]))
    assert on_wishlist() == {1: True, 2: False}


@pytest.mark.parametrize("items", [None, []])
def test_on_wishlist_false_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, pools: None, items: list[WishlistItem] | None
) -> None:
    monkeypatch.setattr(steam_api, "get_wishlist", FakeSteam(items))
    assert on_wishlist() == {1: False, 2: False}


def test_backlog_picks_have_no_wishlist_field(df_table: DfTable) -> None:
    [pick] = tools.compact(rec.score_backlog([(1, "Owned", {"RPG": 10})], {"RPG": 1.0}, df_table), 1)
    assert "on_wishlist" not in pick
