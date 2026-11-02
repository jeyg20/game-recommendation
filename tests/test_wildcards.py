"""Wildcards: picks from the tags just outside the main pools, none of which carry a main pool tag up front."""

import math
from typing import cast

import pytest

from src import recommend as rec
from src import tools
from src.recommend import adjacent_tags, wildcards
from src.steamspy import GameSummary
from src.tag_profile import CorpusRecord, DfTable

MAIN = [f"Main {i}" for i in range(1, 9)]  # profile ranks 1-8: the main pool tags
FILLER = ["F1", "F2", "F3", "F4"]

# profile rank 9 is a generic tag, so the adjacent tags are ranks 10-12; rank 13 is past WILDCARD_POOLS
PROFILE = {tag: 100.0 - n for n, tag in enumerate([*MAIN, "Generic", "Wide 1", "Wide 2", "Wide 3", "Wide 4"])}

# appid -> (full tags, pools it's listed in)
GAMES: dict[int, tuple[dict[str, int], list[str]]] = {
    1: ({"Main 1": 10, "Main 2": 5}, ["Main 1", "Main 2"]),
    2: ({"Main 2": 10, "Main 3": 5}, ["Main 2"]),
    # a main pick whose top 5 skip the pool tags: excluded only because it's already a main pick
    3: ({"Wide 3": 50, "F1": 40, "F2": 30, "F3": 20, "F4": 10, "Main 1": 5}, ["Main 1", "Wide 3"]),
    10: ({"Wide 1": 10, "Wide 2": 5}, ["Wide 1"]),  # a wildcard
    11: ({"Wide 1": 10, "Main 1": 9}, ["Wide 1"]),  # a pool tag among its strongest
    12: ({"Wide 2": 10, "Horror": 9}, ["Wide 2"]),  # dropped when Horror is avoided
    13: ({"Wide 1": 10}, ["Wide 1"]),  # owned
    14: ({"Wide 2": 50, "F1": 40, "F2": 30, "F3": 20, "F4": 10, "Main 1": 5}, ["Wide 2", "Wide 1"]),  # pool tag 6th
    15: ({"Wide 4": 10}, ["Wide 4"]),  # only in a pool past WILDCARD_POOLS
}
SHOVELWARE = 16  # would qualify, but fails the review floor


def listing(appid: int, positive: int = 900) -> GameSummary:
    return cast(
        GameSummary,
        {
            "appid": appid,
            "name": f"Game {appid}",
            "positive": positive,
            "negative": 100,
            "price": "999",
            "discount": "",
        },
    )


@pytest.fixture
def wide_df_table() -> DfTable:
    """Every tag equally distinctive except Generic, which nearly every game carries."""
    n = 1000
    df = {tag: 50 for tag in [*PROFILE, *FILLER, "Horror"]} | {"Generic": 900}
    return {"n": n, "df": df, "idf": {tag: math.log((1 + n) / (1 + count)) + 1 for tag, count in df.items()}}


@pytest.fixture
def fake_pools(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake SteamSpy: pools and tags from GAMES. Returns the tags whose pools were asked for."""
    asked: list[str] = []

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        asked.extend(tags)
        pools: dict[str, dict[str, GameSummary]] = {}
        for tag in tags:
            pools[tag] = {str(appid): listing(appid) for appid, (_, listed) in GAMES.items() if tag in listed}
        if "Wide 2" in tags:
            pools["Wide 2"][str(SHOVELWARE)] = listing(SHOVELWARE, positive=10)
        return {tag: pool for tag, pool in pools.items() if pool}, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        tags = {appid: game[0] for appid, game in GAMES.items()} | {SHOVELWARE: {"Wide 2": 10}}
        return {a: {"appid": a, "genre": "", "tags": tags[a], "fetched_at": "", "status": "ok"} for a in appids}

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    return asked


def test_adjacent_tags_skip_the_main_and_generic_tags(wide_df_table: DfTable) -> None:
    assert adjacent_tags(PROFILE, wide_df_table) == ["Wide 1", "Wide 2", "Wide 3"]


def test_adjacent_tags_empty_for_a_narrow_profile(wide_df_table: DfTable) -> None:
    assert adjacent_tags({tag: 1.0 for tag in MAIN}, wide_df_table) == []


def test_wildcards_keep_only_games_outside_the_bubble(fake_pools: list[str], wide_df_table: DfTable) -> None:
    picks = wildcards(PROFILE, MAIN, {13, 1, 2, 3}, wide_df_table, ["Horror"])
    assert fake_pools == ["Wide 1", "Wide 2", "Wide 3"]
    assert {pick["appid"] for pick in picks} == {10, 14}
    assert {pick["appid"]: pick["via_tag"] for pick in picks} == {10: "Wide 1", 14: "Wide 1"}
    assert all(pick["price_usd"] == 9.99 and pick["positive_ratio"] == 0.9 for pick in picks)
    assert [pick["score"] for pick in picks] == sorted((pick["score"] for pick in picks), reverse=True)


def test_wildcards_without_avoid_tags_keep_the_avoided_game(fake_pools: list[str], wide_df_table: DfTable) -> None:
    assert 12 in {pick["appid"] for pick in wildcards(PROFILE, MAIN, set(), wide_df_table, [])}


@pytest.fixture
def library(wide_df_table: DfTable) -> None:
    owned = [{"appid": 13, "name": "Game 13", "playtime_forever": 600, "rtime_last_played": 0}]
    tools.state |= {"player_games": owned, "games": {}, "df_table": wide_df_table, "profile": PROFILE}


def test_recommend_games_adds_wildcards_apart_from_the_main_picks(fake_pools: list[str], library: None) -> None:
    result = tools.recommend_games(include_backlog=False)
    main = {pick["appid"] for pick in cast(list[dict[str, object]], result["recommendations"])}
    wild = cast(list[dict[str, object]], result["wildcards"])
    assert main == {1, 2, 3}
    assert {pick["appid"] for pick in wild} == {10, 12, 14}
    assert all(pick["on_wishlist"] is False and pick["via_tag"] for pick in wild)


def test_include_wildcards_false_leaves_the_rest_identical(fake_pools: list[str], library: None) -> None:
    with_wildcards = tools.recommend_games(include_backlog=False)
    without = tools.recommend_games(include_backlog=False, include_wildcards=False)
    assert "wildcards" not in without
    assert {key: value for key, value in with_wildcards.items() if key != "wildcards"} == without


def test_no_wildcards_when_boost_tags_are_required(fake_pools: list[str], library: None) -> None:
    result = tools.recommend_games(boost_tags=["Main 1"], include_backlog=False)
    assert "wildcards" not in result
    assert not {"Wide 1", "Wide 2", "Wide 3"} & set(fake_pools)


def test_on_sale_only_applies_to_wildcards(fake_pools: list[str], library: None) -> None:
    assert tools.recommend_games(include_backlog=False, on_sale_only=True)["wildcards"] == []


def test_wildcards_render_as_a_third_table(fake_pools: list[str], library: None) -> None:
    from src.ui import recommendation_tables

    _, wild = recommendation_tables(tools.recommend_games(include_backlog=False)).split("Wildcards")
    assert "Game 10" in wild and "Game 1 " not in wild


def test_near_duplicate_wildcards_are_spread(monkeypatch: pytest.MonkeyPatch, wide_df_table: DfTable) -> None:
    """One dense adjacent pool (three BioShocks from "Underwater") shouldn't fill every wildcard slot."""
    clones = {appid: {"Wide 1": 10, "Wide 2": 9, "Wide 3": 8} for appid in (20, 21, 22)}
    different = {23: {"Wide 1": 10, "Wide 2": 9, "Wide 4": 8}}  # fits a touch less (Wide 4 < Wide 3 in the profile)
    tags = clones | different

    def fetch_tag_pools(asked: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        return {tag: {str(appid): listing(appid) for appid in tags} for tag in asked}, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {a: {"appid": a, "genre": "", "tags": tags[a], "fetched_at": "", "status": "ok"} for a in appids}

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    picks = wildcards(PROFILE, MAIN, set(), wide_df_table, [])
    by_fit = sorted(picks, key=lambda pick: pick["score"], reverse=True)
    assert by_fit[-1]["appid"] == 23, "the different game fits least, so pure fit order would put it last"
    assert 23 in [pick["appid"] for pick in picks[:2]]
