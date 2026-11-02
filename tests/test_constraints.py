from types import SimpleNamespace
from typing import cast

import pytest

from src import config, constraints, hltb, storage, tools
from src import recommend as rec
from src.library import LibraryGame
from src.steamspy import GameSummary
from src.tag_profile import CorpusRecord, DfTable
from src.ui import table

# appid -> (price in cents, HLTB main story hours; None = HLTB has no time, 0 = HLTB's own "no time")
GAMES: dict[int, tuple[str, float | None]] = {
    1: ("2999", 8.0),  # too expensive for $20
    2: ("1499", 30.0),  # too long for 15 h
    3: ("0", 5.0),
    4: ("", 5.0),  # no price
    5: ("999", None),
    6: ("1999", 15.0),
    7: ("499", 0),
}


def listing(appid: int, cents: str) -> GameSummary:
    return cast(
        GameSummary,
        {"appid": appid, "name": f"Game {appid}", "positive": 900, "negative": 100, "price": cents, "discount": "0"},
    )


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> list[str]:
    """One RPG pool of the games above, ranked by appid (lower appid = more RPG votes = better fit).

    Returns the names HLTB was asked for, so tests can count lookups.
    """

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        return {"RPG": {str(appid): listing(appid, cents) for appid, (cents, _) in GAMES.items()}}, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {
            a: {"appid": a, "genre": "", "tags": {"RPG": 100 - a, "Indie": a}, "fetched_at": "", "status": "ok"}
            for a in appids
        }

    asked: list[str] = []

    async def get_game_hltb(name: str) -> SimpleNamespace | None:
        asked.append(name)
        hours = GAMES[int(name.removeprefix("Game "))][1]
        return SimpleNamespace(main_story=hours) if hours is not None else None

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    monkeypatch.setattr(hltb, "get_game_hltb", get_game_hltb)
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": {"RPG": 1.0}}
    return asked


def picks(result: dict[str, object], key: str = "recommendations") -> list[dict[str, object]]:
    return cast(list[dict[str, object]], result[key])


def test_every_pick_respects_the_price_limit(pools: list[str]) -> None:
    result = tools.recommend_games(include_backlog=False, max_price_usd=20)
    assert {p["appid"] for p in picks(result)} == {2, 3, 5, 6, 7}  # 1 costs more, 4 has no known price
    assert all(cast(float, p["price_usd"]) <= 20 for p in picks(result))
    assert cast(dict[str, object], result["applied"])["max_price_usd"] == 20
    assert pools == []  # no length limit: HLTB is never asked


def test_known_lengths_fit_and_unknown_ones_come_after(pools: list[str]) -> None:
    result = tools.recommend_games(include_backlog=False, max_hours=15)
    order = [p["appid"] for p in picks(result)]
    assert order[:4] == [1, 3, 4, 6] and set(order[4:]) == {5, 7}  # 2 is too long; 0 h counts as unknown
    by_appid = {p["appid"]: p for p in picks(result)}
    assert by_appid[1]["main_story_hours"] == 8.0 and "length_unknown" not in by_appid[1]
    assert by_appid[5]["length_unknown"] is True and "main_story_hours" not in by_appid[5]


def test_lookups_are_capped_and_the_unchecked_tail_dropped(pools: list[str]) -> None:
    result = tools.recommend_games(include_backlog=False, max_hours=15, limit=1)
    assert len(pools) == constraints.LENGTH_LOOKUP_FACTOR  # the best 3 picks only
    assert [p["appid"] for p in picks(result)] == [1]


def test_a_second_call_makes_no_hltb_requests(pools: list[str]) -> None:
    first = tools.recommend_games(include_backlog=False, max_hours=15)
    asked = len(pools)
    assert asked == len(GAMES)
    second = tools.recommend_games(include_backlog=False, max_hours=15)
    assert len(pools) == asked
    assert picks(first) == picks(second)


def test_stale_cache_entries_are_looked_up_again(pools: list[str]) -> None:
    storage.write_json(config.CANDIDATE_HLTB_PATH, {"1": {"hours": 99.0, "fetched_at": 0.0}})
    tools.recommend_games(include_backlog=False, max_hours=15, limit=1)
    assert "Game 1" in pools


def test_noted_limits_apply_until_removed_or_overridden(pools: list[str]) -> None:
    tools.note_request(max_price_usd=10)
    assert {p["appid"] for p in picks(tools.recommend_games(include_backlog=False))} == {3, 5, 7}
    assert {p["appid"] for p in picks(tools.recommend_games(include_backlog=False, max_price_usd=20))} == {
        2,
        3,
        5,
        6,
        7,
    }
    tools.note_request(remove=["max_price_usd"])
    assert tools.state["draft"]["max_price_usd"] is None
    assert len(picks(tools.recommend_games(include_backlog=False))) == len(GAMES)


def test_no_limits_leave_the_output_unchanged(pools: list[str]) -> None:
    result = tools.recommend_games(include_backlog=False)
    applied = cast(dict[str, object], result["applied"])
    assert "max_hours" not in applied and "max_price_usd" not in applied
    assert all("main_story_hours" not in p and "length_unknown" not in p for p in picks(result))
    assert pools == []


def test_backlog_uses_the_library_lengths(pools: list[str]) -> None:
    def owned(appid: int, hours: float | None) -> LibraryGame:
        return {
            "appid": appid,
            "name": f"Owned {appid}",
            "playtime_hours": 0.0,
            "main_story_hours": hours,
            "review_score": None,
            "genres": [],
            "tags": {"RPG": 10},
        }

    tools.state["games"] = {100: owned(100, 40.0), 101: owned(101, 6.0), 102: owned(102, None)}
    result = tools.recommend_games(max_hours=15, max_price_usd=20)
    backlog = picks(result, "backlog")
    assert [p["appid"] for p in backlog] == [101, 102]  # price doesn't apply: owned
    assert backlog[0]["main_story_hours"] == 6.0 and backlog[1]["length_unknown"] is True
    assert not any(name.startswith("Owned") for name in pools)  # owned lengths come from the library, not HLTB


def test_wildcards_respect_the_limits(monkeypatch: pytest.MonkeyPatch, pools: list[str]) -> None:
    wild: list[rec.Wildcard] = [
        {
            "appid": appid,
            "name": f"Game {appid}",
            "score": 0.5,
            "positive_ratio": 0.9,
            "matched_tags": [],
            "price_usd": rec.price_usd(listing(appid, cents)),
            "discount_percent": 0,
            "via_tag": "Puzzle",
        }
        for appid, (cents, _) in GAMES.items()
    ]
    monkeypatch.setattr(tools, "wildcards", lambda *args: wild)
    result = tools.recommend_games(include_backlog=False, max_price_usd=20, max_hours=15)
    assert [p["appid"] for p in picks(result, "wildcards")] == [3, 6, 5]  # 1 too dear, 2 too long, 4 unpriced


def test_table_shows_the_length_column_only_when_checked() -> None:
    pick = {"name": "A", "appid": 1, "score": 0.5, "positive_ratio": 0.9, "matched_tags": ["RPG"]}
    assert "Story" not in table("T", [pick], "Liked", "positive_ratio")
    shown = table("T", [pick | {"main_story_hours": 8.0}, pick | {"length_unknown": True}], "Liked", "positive_ratio")
    assert "Story" in shown and "8.0h" in shown
