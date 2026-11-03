"""Offline evaluation: hide each of the player's strongest games and see where the ranking puts it.

A number to compare ranking changes by, instead of eyeballing picks. Leave-one-out over two held-out sets: the
favourites (top owned games by plain taste weight) and the recent set (the most recently played games with real
play). For each game the profile is rebuilt without it, the game is un-owned, and its rank in `recommend` is read.
The favourites set penalises ideas that fade old games (recency); the recent set rewards them. Diversity is measured
once, on the full profile's top picks.
"""

import time
from collections.abc import Collection, Mapping
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Literal, TypedDict

from src import recommend as rec
from src.config import EVAL_DIR
from src.library import LibraryGame
from src.recommend import Recommendation, passes_floor
from src.steamspy import GameSummary
from src.storage import write_json
from src.tag_profile import DfTable, TagVector, cosine, norm, tag_vector, taste_weight, top_tags
from src.tools import is_abandoned, library_profile
from src.ui import status

HOLDOUT_GAMES = 10
POOL_TAGS = 8  # the same top_tags(profile, 8) that recommend_games retrieves with
RECALL_AT = (10, 25)
DIVERSITY_PICKS = 10
RECENT_MIN_HOURS = 5  # the recent set wants real play, not a launch to look at the menu

EvalSet = Literal["favourites", "recent"]
EVAL_SETS: tuple[EvalSet, ...] = ("favourites", "recent")


class HeldOutGame(TypedDict):
    appid: int
    name: str
    taste_weight: float
    rank: int | None  # 1-based position in the recommendations without it, None when never retrieved


class Summary(TypedDict):
    held_out: int
    recall_at_10: float
    recall_at_25: float
    mrr: float  # mean reciprocal rank, a miss counts 0
    never_retrieved: int
    diversity: float | None  # mean pairwise cosine of the full profile's top picks; lower = more varied


class EvalResult(TypedDict):
    label: str
    created_at: str
    summary: Summary | None  # the favourites set; None when it wasn't run
    games: list[HeldOutGame]
    recent_summary: Summary | None  # None when not run, or skipped for lack of last-played dates
    recent_games: list[HeldOutGame]
    top_picks: list[str]  # the full profile's picks that diversity was measured on


def weight_of(game: LibraryGame) -> float:
    return taste_weight(game["playtime_hours"], game["main_story_hours"], game["tags"] or {})


def findable(games: dict[int, LibraryGame], listings: dict[int, GameSummary]) -> list[LibraryGame]:
    """Games the ranking could find at all: tagged, in one of the full profile's tag pools, past the shovelware floor.

    Holding out anything else would measure retrieval coverage, not ranking.
    """
    return [
        game
        for game in games.values()
        if game["tags"] and game["appid"] in listings and passes_floor(listings[game["appid"]])
    ]


def pick_held_out(games: dict[int, LibraryGame], listings: dict[int, GameSummary], n: int) -> list[LibraryGame]:
    """The n strongest findable games by plain taste weight (all-time favourites). Ties break on appid."""
    return sorted(findable(games, listings), key=lambda game: (-weight_of(game), game["appid"]))[:n]


def pick_recent(
    games: dict[int, LibraryGame],
    listings: dict[int, GameSummary],
    last_played: Mapping[int, int],
    n: int,
    now: float,
) -> list[LibraryGame]:
    """The n findable games last played most recently, among those with real play (RECENT_MIN_HOURS, not abandoned).

    What the player is into now, so an idea that fades old favourites can be judged on what it should help. A game
    with no last-played date can't be placed in time and is left out. Ties break on appid.
    """
    played = [
        game
        for game in findable(games, listings)
        if game["playtime_hours"] >= RECENT_MIN_HOURS
        and last_played.get(game["appid"], 0)
        and not is_abandoned(game, last_played[game["appid"]], now)
    ]
    played.sort(key=lambda game: (-last_played[game["appid"]], game["appid"]))
    return played[:n]


def rank_of(appid: int, recommendations: list[Recommendation]) -> int | None:
    return next((i for i, pick in enumerate(recommendations, start=1) if pick["appid"] == appid), None)


def mean_pairwise_cosine(vectors: list[TagVector]) -> float | None:
    """Mean cosine over every pair; None with fewer than two vectors."""
    pairs = list(combinations(vectors, 2))
    if not pairs:
        return None
    return sum(cosine(a, norm(a), b) for a, b in pairs) / len(pairs)


def summarize(ranks: list[int | None], diversity: float | None) -> Summary:
    found = [rank for rank in ranks if rank is not None]
    count = len(ranks) or 1

    def recall(k: int) -> float:
        return sum(1 for rank in found if rank <= k) / count

    return {
        "held_out": len(ranks),
        "recall_at_10": recall(RECALL_AT[0]),
        "recall_at_25": recall(RECALL_AT[1]),
        "mrr": sum(1 / rank for rank in found) / count,
        "never_retrieved": len(ranks) - len(found),
        "diversity": diversity,
    }


def evaluate(
    owned_appids: set[int],
    games: dict[int, LibraryGame],
    df_table: DfTable,
    label: str = "",
    last_played: Mapping[int, int] | None = None,
    recent_appids: Collection[int] = (),
    achievement_share: Mapping[int, float] | None = None,
    sets: Collection[EvalSet] = EVAL_SETS,
) -> EvalResult:
    """Run the leave-one-out over `games` (the enriched library); `owned_appids` is every owned appid, as excluded.

    `last_played` (appid -> rtime_last_played) lets the profile count abandoned games against their tags, as live.
    It and `recent_appids` (played in the last two weeks) also fade old favourites' weights, as live.
    `achievement_share` (appid -> unlocked share) counts achievements towards completion, as live.
    `sets` picks the held-out sets to run; the recent set needs `last_played` and is skipped without it.
    """

    def profile_of(library: Collection[LibraryGame]) -> TagVector:
        return library_profile(
            library, df_table, last_played, recent_appids=recent_appids, achievement_share=achievement_share
        )

    def hold_out(held_out: list[LibraryGame]) -> list[HeldOutGame]:
        results: list[HeldOutGame] = []
        for n, game in enumerate(held_out, start=1):
            status.update(f"ranking without {game['name']}", n, len(held_out))
            without = profile_of([other for other in games.values() if other["appid"] != game["appid"]])
            ranked = rec.recommend(without, top_tags(without, POOL_TAGS), owned_appids - {game["appid"]}, df_table)
            results.append(
                {
                    "appid": game["appid"],
                    "name": game["name"],
                    "taste_weight": weight_of(game),
                    "rank": rank_of(game["appid"], ranked["recommendations"]),
                }
            )
        return results

    profile = profile_of(list(games.values()))
    pools, _ = rec.fetch_tag_pools(top_tags(profile, POOL_TAGS))
    listings = {int(game["appid"]): game for pool in pools.values() for game in pool.values()}

    favourites = hold_out(pick_held_out(games, listings, HOLDOUT_GAMES)) if "favourites" in sets else None
    recent: list[HeldOutGame] | None = None
    if "recent" in sets:
        if last_played:
            recent = hold_out(pick_recent(games, listings, last_played, HOLDOUT_GAMES, time.time()))
        else:
            status.say("no last-played dates, so the recent set is skipped")

    status.update("ranking with the full profile, for diversity")
    picks = rec.recommend(profile, top_tags(profile, POOL_TAGS), owned_appids, df_table)["recommendations"]
    picks = picks[:DIVERSITY_PICKS]
    records = rec.enrich_tags([pick["appid"] for pick in picks])
    vectors = [tag_vector(records[pick["appid"]]["tags"], df_table) for pick in picks if pick["appid"] in records]
    diversity = mean_pairwise_cosine(vectors)

    def summary_of(results: list[HeldOutGame] | None) -> Summary | None:
        return None if results is None else summarize([result["rank"] for result in results], diversity)

    return {
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary_of(favourites),
        "games": favourites or [],
        "recent_summary": summary_of(recent),
        "recent_games": recent or [],
        "top_picks": [pick["name"] for pick in picks],
    }


def save(result: EvalResult) -> Path:
    """Write the run to EVAL_DIR/<timestamp>.json and return the path."""
    path = EVAL_DIR / f"{result['created_at'].replace(':', '-')}.json"
    write_json(path, result)
    return path
