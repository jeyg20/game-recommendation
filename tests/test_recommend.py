from datetime import date, timedelta
from typing import cast

import pytest

from src import recommend as rec
from src.recommend import (
    MIN_STRICT_RESULTS,
    Recommendation,
    diversify,
    fits_tags,
    has_avoided_tag,
    has_required_tags,
    passes_floor,
    pick_finalists,
)
from src.steamspy import GameSummary
from src.storage import append_jsonl, read_json, write_json
from src.tag_profile import UNTAGGED_RETRY_DAYS, CorpusRecord, DfTable, load_cache_status, needs_fetch


def game(appid: int, positive: int = 900, negative: int = 100, name: str = "") -> GameSummary:
    """A SteamSpy listing entry; only the fields the ranking reads matter."""
    return cast(
        GameSummary, {"appid": appid, "name": name or f"Game {appid}", "positive": positive, "negative": negative}
    )


def test_passes_floor_needs_enough_reviews_and_ratio() -> None:
    assert passes_floor(game(1, positive=400, negative=100))  # 500 reviews, 80%
    assert not passes_floor(game(1, positive=399, negative=100))  # 499 reviews
    assert not passes_floor(game(1, positive=740, negative=260))  # 74%
    assert not passes_floor(game(1, positive=0, negative=0))


def test_pick_finalists_ranks_by_pool_hits_then_positive_reviews() -> None:
    pools = {
        "RPG": {"1": game(1, positive=1000), "2": game(2, positive=5000), "3": game(3)},
        "Indie": {"1": game(1, positive=1000), "3": game(3)},
        "Puzzle": {"1": game(1, positive=1000)},
    }
    assert [g["appid"] for g in pick_finalists(pools, set())] == [1, 3, 2]


def test_pick_finalists_drops_excluded_shovelware_and_disallowed() -> None:
    pools = {"RPG": {"1": game(1), "2": game(2, positive=10), "3": game(3), "4": game(4)}}
    assert [g["appid"] for g in pick_finalists(pools, {1})] == [3, 4]
    assert [g["appid"] for g in pick_finalists(pools, {1}, allowed_appids={4})] == [4]


def test_has_required_tags_strict_and_relaxed() -> None:
    tags = {"Shooter": 50, "Co-op": 40, "Action": 30}
    assert has_required_tags(tags, ["shooter", "co-op"], relaxed=False)
    assert not has_required_tags(tags, ["Shooter", "Horror"], relaxed=False)
    assert has_required_tags(tags, ["Shooter", "Horror"], relaxed=True)


def test_has_required_tags_ignores_footnote_tags() -> None:
    tags = {f"Tag {i}": 100 - i for i in range(10)} | {"Shooter": 1}
    assert not has_required_tags(tags, ["Shooter"], relaxed=False)


def test_has_avoided_tag_only_among_the_strongest() -> None:
    assert has_avoided_tag({"Horror": 50, "Action": 40}, ["horror"])
    footnote = {f"Tag {i}": 100 - i for i in range(5)} | {"Horror": 1}
    assert not has_avoided_tag(footnote, ["Horror"])


def test_fits_tags_combines_both_rules() -> None:
    tags = {"Shooter": 50, "Horror": 40}
    assert fits_tags(tags, [], [], relaxed=False)
    assert fits_tags(tags, ["Shooter"], [], relaxed=False)
    assert not fits_tags(tags, ["Shooter"], ["Horror"], relaxed=False)
    assert not fits_tags(tags, ["Puzzle"], [], relaxed=False)


def pick(appid: int, score: float) -> Recommendation:
    return {"appid": appid, "name": f"Game {appid}", "score": score, "positive_ratio": 0.9, "matched_tags": []}


# three near-duplicate survival-crafts and one different game that fits slightly worse
SURVIVAL = {"Survival": 1.0, "Crafting": 1.0, "Open World": 1.0}
NEAR_DUPLICATES = {1: SURVIVAL, 2: SURVIVAL | {"Base Building": 0.1}, 3: SURVIVAL | {"Dinosaurs": 0.1}}
DIFFERENT = {4: {"Roguelike": 1.0, "Deckbuilding": 1.0, "Crafting": 0.2}}


def test_diversify_moves_the_different_game_up() -> None:
    picks = [pick(1, 0.90), pick(2, 0.89), pick(3, 0.88), pick(4, 0.80)]
    order = diversify(picks, NEAR_DUPLICATES | DIFFERENT)
    assert [rec["appid"] for rec in order] == [1, 4, 2, 3]
    assert [rec["score"] for rec in order] == [0.90, 0.80, 0.89, 0.88]  # the fit scores are untouched


def test_diversify_with_lambda_one_keeps_the_fit_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec, "DIVERSITY_LAMBDA", 1.0)
    picks = [pick(1, 0.90), pick(2, 0.89), pick(3, 0.88), pick(4, 0.80)]
    assert diversify(picks, NEAR_DUPLICATES | DIFFERENT) == picks


def test_diversify_leaves_the_tail_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec, "DIVERSITY_POOL", 3)
    picks = [pick(1, 0.90), pick(2, 0.89), pick(3, 0.88), pick(4, 0.80)]
    vectors = {appid: vector for appid, vector in (NEAR_DUPLICATES | DIFFERENT).items() if appid <= 3}
    assert [rec["appid"] for rec in diversify(picks, vectors)] == [1, 2, 3, 4]


# ---------------------------------------------------------------------
# recommend(): pools and tags are stubbed, so these exercise the strict / relaxed / unenforced paths offline
# ---------------------------------------------------------------------
def record(appid: int, tags: dict[str, int]) -> CorpusRecord:
    return {"appid": appid, "genre": "", "tags": tags, "fetched_at": "2026-01-01", "status": "ok"}


@pytest.fixture
def stub_steamspy(monkeypatch: pytest.MonkeyPatch) -> dict[int, dict[str, int]]:
    """appid -> tags served by enrich_tags; pools come from the same table (a game is in a tag's pool if it has it)."""
    tags_by_appid: dict[int, dict[str, int]] = {}

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        pools: dict[str, dict[str, GameSummary]] = {}
        for tag in tags:
            pool = {str(appid): game(appid) for appid, game_tags in tags_by_appid.items() if tag in game_tags}
            if pool:  # the real fetcher leaves out failed and empty pools
                pools[tag] = pool
        return pools, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {appid: record(appid, tags_by_appid[appid]) for appid in appids if appid in tags_by_appid}

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    return tags_by_appid


def test_recommend_without_requirements_ranks_by_cosine(
    stub_steamspy: dict[int, dict[str, int]], df_table: DfTable
) -> None:
    stub_steamspy |= {1: {"RPG": 10}, 2: {"RPG": 10, "Horror": 10}, 3: {"Horror": 10, "RPG": 1}}
    result = rec.recommend({"RPG": 1.0}, ["RPG", "Horror"], set(), df_table)
    assert [r["appid"] for r in result["recommendations"]] == [1, 2, 3]
    assert result["recommendations"][0]["score"] == pytest.approx(1.0)
    assert result["recommendations"][0]["positive_ratio"] == pytest.approx(0.9)
    assert not result["requirement_relaxed"]
    assert result["requirement_unenforced"] == []


def test_recommend_spreads_near_duplicates(stub_steamspy: dict[int, dict[str, int]], df_table: DfTable) -> None:
    stub_steamspy |= {
        1: {"RPG": 10, "Co-op": 10},
        2: {"RPG": 10, "Co-op": 9},
        3: {"RPG": 9, "Co-op": 10},
        4: {"RPG": 10, "Horror": 10},
    }
    profile = {"RPG": 1.0, "Co-op": 1.0, "Horror": 0.9}
    result = rec.recommend(profile, ["RPG", "Co-op", "Horror"], set(), df_table)
    by_fit = [r["appid"] for r in sorted(result["recommendations"], key=lambda r: r["score"], reverse=True)]
    assert by_fit[-1] == 4  # the worst fit of the four...
    assert [r["appid"] for r in result["recommendations"]][:2] == [by_fit[0], 4]  # ...is placed second


def test_recommend_drops_excluded_and_avoided(stub_steamspy: dict[int, dict[str, int]], df_table: DfTable) -> None:
    stub_steamspy |= {1: {"RPG": 10}, 2: {"RPG": 10, "Horror": 20}, 3: {"RPG": 10, "Indie": 5}}
    result = rec.recommend({"RPG": 1.0}, ["RPG"], {3}, df_table, avoid_tags=["Horror"])
    assert [r["appid"] for r in result["recommendations"]] == [1]


def test_recommend_strict_requires_every_tag(stub_steamspy: dict[int, dict[str, int]], df_table: DfTable) -> None:
    both = {appid: {"Shooter": 10, "Co-op": 10} for appid in range(1, MIN_STRICT_RESULTS + 1)}
    stub_steamspy |= both | {100: {"Shooter": 10, "RPG": 10}, 101: {"RPG": 10}}
    result = rec.recommend({"RPG": 1.0}, ["RPG"], set(), df_table, required_tags=["Shooter", "Co-op"])
    assert {r["appid"] for r in result["recommendations"]} == set(both)
    assert result["pool_tags"][:2] == ["Shooter", "Co-op"]
    assert not result["requirement_relaxed"]


def test_recommend_relaxes_when_too_few_have_every_tag(
    stub_steamspy: dict[int, dict[str, int]], df_table: DfTable
) -> None:
    stub_steamspy |= {1: {"Shooter": 10, "Co-op": 10}, 2: {"Shooter": 10}, 3: {"Co-op": 10}, 4: {"RPG": 10}}
    result = rec.recommend({"RPG": 1.0}, ["RPG"], set(), df_table, required_tags=["Shooter", "Co-op"])
    assert {r["appid"] for r in result["recommendations"]} == {1, 2, 3}
    assert result["requirement_relaxed"]


def test_recommend_never_relaxes_a_single_requirement(
    stub_steamspy: dict[int, dict[str, int]], df_table: DfTable
) -> None:
    stub_steamspy |= {1: {"Shooter": 10}, 2: {"RPG": 10}}
    result = rec.recommend({"RPG": 1.0}, ["RPG"], set(), df_table, required_tags=["Shooter"])
    assert [r["appid"] for r in result["recommendations"]] == [1]
    assert not result["requirement_relaxed"]


def test_recommend_reports_required_tags_with_no_pool(
    stub_steamspy: dict[int, dict[str, int]], df_table: DfTable
) -> None:
    stub_steamspy |= {1: {"RPG": 10}, 2: {"Indie": 10}}
    result = rec.recommend({"RPG": 1.0}, ["RPG", "Indie"], set(), df_table, required_tags=["Nonexistent"])
    assert result["requirement_unenforced"] == ["Nonexistent"]
    assert {r["appid"] for r in result["recommendations"]} == {1, 2}  # nothing left to filter on


@pytest.fixture
def steamspy_tags(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, dict[str, GameSummary] | None], list[str]]:
    """tag -> what a fake get_games_by_tag returns (missing = []), and the list of tags it was asked for."""
    served: dict[str, dict[str, GameSummary] | None] = {}
    requested: list[str] = []

    def get_games_by_tag(tag: str) -> dict[str, GameSummary] | None:
        requested.append(tag)
        return served.get(tag, {})

    monkeypatch.setattr(rec, "get_games_by_tag", get_games_by_tag)
    monkeypatch.setattr(rec.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(rec, "_empty_this_run", {})
    return served, requested


def cache_pool(tag: str, fetched_at: str, games: dict[str, GameSummary]) -> None:
    cache = read_json(rec.TAG_POOLS_PATH) if rec.TAG_POOLS_PATH.is_file() else {}
    write_json(rec.TAG_POOLS_PATH, {**cache, tag: {"fetched_at": fetched_at, "games": games}})


OLD = "2026-10-27"
OLD_POOL = {"1": game(1, name="Old")}


@pytest.mark.parametrize("response", [{}, None], ids=["empty", "failed"])
def test_fetch_tag_pools_falls_back_to_a_stale_pool(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]],
    response: dict[str, GameSummary] | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    served, _ = steamspy_tags
    served["Open World"] = response
    cache_pool("Open World", OLD, OLD_POOL)
    pools, stale = rec.fetch_tag_pools(["Open World"])
    assert pools == {"Open World": OLD_POOL}
    assert stale == {"Open World": OLD}
    assert f"using cached 'Open World' pool from {OLD}" in capsys.readouterr().out
    assert read_json(rec.TAG_POOLS_PATH)["Open World"]["fetched_at"] == OLD  # cache entry left alone


def test_fetch_tag_pools_asks_once_per_run_for_an_empty_tag(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]],
) -> None:
    _, requested = steamspy_tags
    cache_pool("Open World", OLD, OLD_POOL)
    rec.fetch_tag_pools(["Open World"])
    pools, stale = rec.fetch_tag_pools(["Open World"])
    assert requested == ["Open World"]
    assert pools == {"Open World": OLD_POOL} and stale == {"Open World": OLD}


def test_fetch_tag_pools_skips_an_empty_tag_with_no_cache(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]],
) -> None:
    assert rec.fetch_tag_pools(["Nonexistent"]) == ({}, {})
    assert not rec.TAG_POOLS_PATH.is_file()


def test_fetch_tag_pools_uses_a_fresh_pool_without_asking(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]],
) -> None:
    _, requested = steamspy_tags
    cache_pool("RPG", date.today().isoformat(), OLD_POOL)
    assert rec.fetch_tag_pools(["RPG"]) == ({"RPG": OLD_POOL}, {})
    assert requested == []


def test_fetch_tag_pools_replaces_a_stale_entry_once_steamspy_answers(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]],
) -> None:
    served, _ = steamspy_tags
    new_pool = {"2": game(2, name="New")}
    served["Open World"] = new_pool
    cache_pool("Open World", OLD, OLD_POOL)
    assert rec.fetch_tag_pools(["Open World"]) == ({"Open World": new_pool}, {})
    assert read_json(rec.TAG_POOLS_PATH)["Open World"] == {"fetched_at": date.today().isoformat(), "games": new_pool}


def days_old(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def corpus_record(
    appid: int, status: str, fetched_at: str, genre: str = "Action", tags: dict[str, int] | None = None
) -> CorpusRecord:
    record = {"appid": appid, "genre": genre, "tags": tags or {}, "fetched_at": fetched_at, "status": status}
    return cast(CorpusRecord, record)


def test_enrich_tags_refetches_untagged_games_weekly_and_non_games_never(monkeypatch: pytest.MonkeyPatch) -> None:
    for record in [
        corpus_record(1, "untagged", days_old(8)),
        corpus_record(2, "untagged", days_old(2)),
        corpus_record(3, "excluded", days_old(30)),  # saved before `untagged` existed: an untagged game
        corpus_record(4, "excluded", days_old(30), genre="Utilities"),  # a non-game: final
        corpus_record(5, "excluded", days_old(30), tags={"Software": 90, "Utilities": 10}),
    ]:
        append_jsonl(rec.STEAM_BACKGROUND_CORPUS, record)
    asked: list[int] = []

    def get_game_details(appid: int) -> dict[str, object]:
        asked.append(appid)
        return {"genre": "Action", "tags": {"RPG": 50, "Action": 30}}

    monkeypatch.setattr(rec, "get_game_details", get_game_details)
    monkeypatch.setattr(rec.time, "sleep", lambda seconds: None)
    records = rec.enrich_tags([1, 2, 3, 4, 5])

    assert asked == [1, 3]
    assert set(records) == {1, 3}  # tagged now, so rankable
    assert rec.enrich_tags([1, 2, 3, 4, 5]).keys() == {1, 3} and asked == [1, 3]  # and final


def test_a_game_steamspy_has_no_tags_for_is_saved_untagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rec, "get_game_details", lambda appid: {"genre": "Action", "tags": {}})
    monkeypatch.setattr(rec.time, "sleep", lambda seconds: None)
    assert rec.enrich_tags([1]) == {}
    assert load_cache_status(rec.STEAM_BACKGROUND_CORPUS)[1]["status"] == "untagged"


def test_corpus_build_retries_untagged_records_after_a_week() -> None:
    corpus = {
        1: corpus_record(1, "untagged", days_old(UNTAGGED_RETRY_DAYS)),
        2: corpus_record(2, "untagged", days_old(UNTAGGED_RETRY_DAYS - 1)),
        3: corpus_record(3, "excluded", days_old(365), genre="Utilities"),
        4: corpus_record(4, "excluded", days_old(UNTAGGED_RETRY_DAYS)),  # legacy untagged
        5: corpus_record(5, "ok", days_old(365), tags={"RPG": 1}),
    }
    assert [appid for appid in corpus if needs_fetch(appid, corpus)] == [1, 4]


def test_fetch_tag_pools_asks_again_for_an_empty_tag_after_the_retry_window(
    steamspy_tags: tuple[dict[str, dict[str, GameSummary] | None], list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, requested = steamspy_tags
    clock = [1_800_000_000.0]
    monkeypatch.setattr(rec.time, "time", lambda: clock[0])
    cache_pool("Open World", OLD, OLD_POOL)
    rec.fetch_tag_pools(["Open World"])
    clock[0] += rec.EMPTY_RETRY_HOURS * 3600 - 1
    rec.fetch_tag_pools(["Open World"])
    assert requested == ["Open World"]
    clock[0] += 1
    pools, stale = rec.fetch_tag_pools(["Open World"])
    assert requested == ["Open World", "Open World"]
    assert pools == {"Open World": OLD_POOL} and stale == {"Open World": OLD}  # still empty: the old pool serves
