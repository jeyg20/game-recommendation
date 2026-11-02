import json
from typing import cast

import pytest

from src import evaluate as ev
from src import recommend as rec
from src.library import LibraryGame
from src.recommend import Recommendation, RecommendResult
from src.steamspy import GameSummary
from src.tag_profile import CorpusRecord, DfTable, TagVector


def library_game(appid: int, hours: float, tags: dict[str, int] | None) -> LibraryGame:
    return {
        "appid": appid,
        "name": f"Game {appid}",
        "playtime_hours": hours,
        "main_story_hours": None,
        "review_score": None,
        "genres": None,
        "tags": tags,
    }


def listing(appid: int, positive: int = 900, negative: int = 100) -> GameSummary:
    return cast(GameSummary, {"appid": appid, "name": f"Game {appid}", "positive": positive, "negative": negative})


def pick(appid: int) -> Recommendation:
    return {"appid": appid, "name": f"Pick {appid}", "score": 0.5, "positive_ratio": 0.9, "matched_tags": []}


# held-out game -> where the fake ranking puts it once it's hidden (None: never retrieved)
RANK_WHEN_HIDDEN = {1: 1, 2: 20, 3: None}
DIVERSITY_PICK_TAGS = {100: {"RPG": 10}, 101: {"RPG": 10}, 102: {"Horror": 10}}


@pytest.fixture
def library() -> dict[int, LibraryGame]:
    """Three findable games (strongest first), one outside every pool, one under the shovelware floor, one untagged."""
    return {
        1: library_game(1, 200, {"RPG": 10}),
        2: library_game(2, 100, {"RPG": 10, "Roguelike": 5}),
        3: library_game(3, 50, {"Puzzle": 10}),
        4: library_game(4, 500, {"RPG": 10}),  # not in any pool
        5: library_game(5, 400, {"RPG": 10}),  # in a pool, fails the floor
        6: library_game(6, 300, None),  # no tags
    }


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[TagVector, list[str], set[int]]]:
    """Fake pools, recommend and enrich_tags; returns the recommend calls as (profile, top tags, excluded)."""
    seen: list[tuple[TagVector, list[str], set[int]]] = []

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        pools = {"RPG": {"1": listing(1), "2": listing(2), "5": listing(5, positive=10)}, "Puzzle": {"3": listing(3)}}
        return pools, {}

    def recommend(profile: TagVector, top: list[str], excluded: set[int], df_table: DfTable) -> RecommendResult:
        seen.append((profile, top, excluded))
        hidden = [appid for appid in RANK_WHEN_HIDDEN if appid not in excluded]
        if not hidden:  # the full-profile run
            picks = [pick(appid) for appid in DIVERSITY_PICK_TAGS]
        else:
            rank = RANK_WHEN_HIDDEN[hidden[0]]
            picks = [pick(1000 + i) for i in range(30)]
            if rank is not None:
                picks.insert(rank - 1, pick(hidden[0]))
        return {
            "recommendations": picks,
            "pool_tags": top,
            "requirement_relaxed": False,
            "requirement_unenforced": [],
            "stale_pools": {},
        }

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {
            appid: {"appid": appid, "genre": "", "tags": DIVERSITY_PICK_TAGS[appid], "fetched_at": "", "status": "ok"}
            for appid in appids
        }

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "recommend", recommend)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    return seen


OWNED = {1, 2, 3, 4, 5, 6, 7}  # 7: owned but not in the enriched library (a non-game)


def test_ranks_and_metrics(
    library: dict[int, LibraryGame], calls: list[tuple[TagVector, list[str], set[int]]], df_table: DfTable
) -> None:
    result = ev.evaluate(OWNED, library, df_table, label="before")

    assert [(g["appid"], g["rank"]) for g in result["games"]] == [(1, 1), (2, 20), (3, None)]
    summary = result["summary"]
    assert summary["held_out"] == 3
    assert summary["recall_at_10"] == pytest.approx(1 / 3)
    assert summary["recall_at_25"] == pytest.approx(2 / 3)
    assert summary["mrr"] == pytest.approx((1 + 1 / 20) / 3)
    assert summary["never_retrieved"] == 1
    assert summary["diversity"] == pytest.approx(1 / 3)  # pairs: RPG-RPG 1, RPG-Horror 0, RPG-Horror 0
    assert result["label"] == "before"


def test_each_run_hides_one_game_from_profile_and_exclusions(
    library: dict[int, LibraryGame], calls: list[tuple[TagVector, list[str], set[int]]], df_table: DfTable
) -> None:
    ev.evaluate(OWNED, library, df_table)

    held_out_calls, full = calls[:3], calls[3]
    assert [excluded for _, _, excluded in held_out_calls] == [OWNED - {1}, OWNED - {2}, OWNED - {3}]
    assert "Puzzle" not in held_out_calls[2][0]  # game 3 is the only Puzzle game
    assert "Roguelike" not in held_out_calls[1][0]
    assert full[2] == OWNED
    assert all(top == sorted(profile, key=profile.__getitem__, reverse=True)[:8] for profile, top, _ in calls)


def test_held_out_set_is_capped(
    library: dict[int, LibraryGame],
    calls: list[tuple[TagVector, list[str], set[int]]],
    df_table: DfTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ev, "HOLDOUT_GAMES", 2)
    assert [g["appid"] for g in ev.evaluate(OWNED, library, df_table)["games"]] == [1, 2]


def test_mean_pairwise_cosine_needs_two() -> None:
    assert ev.mean_pairwise_cosine([{"RPG": 1.0}]) is None
    assert ev.mean_pairwise_cosine([{"RPG": 1.0}, {"RPG": 2.0}]) == pytest.approx(1.0)


def test_summarize_with_nothing_held_out() -> None:
    assert ev.summarize([], None)["mrr"] == 0.0


def test_save_writes_under_eval_dir(
    library: dict[int, LibraryGame], calls: list[tuple[TagVector, list[str], set[int]]], df_table: DfTable
) -> None:
    result = ev.evaluate(OWNED, library, df_table, label="x")
    path = ev.save(result)
    assert path.parent == ev.EVAL_DIR
    assert json.loads(path.read_text())["label"] == "x"
