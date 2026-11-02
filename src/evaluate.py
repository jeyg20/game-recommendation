"""Offline evaluation: hide each of the player's strongest games and see where the ranking puts it.

A number to compare ranking changes by, instead of eyeballing picks. Leave-one-out over the top owned games by taste
weight: the profile is rebuilt without the held-out game, the game is un-owned, and its rank in `recommend` is read.
Diversity is measured separately on the full profile's top picks.
"""

from collections.abc import Mapping
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import TypedDict

from src import recommend as rec
from src.config import EVAL_DIR
from src.library import LibraryGame
from src.recommend import Recommendation, passes_floor
from src.steamspy import GameSummary
from src.storage import write_json
from src.tag_profile import DfTable, TagVector, cosine, norm, tag_vector, taste_weight, top_tags
from src.tools import library_profile
from src.ui import status

HOLDOUT_GAMES = 10
POOL_TAGS = 8  # the same top_tags(profile, 8) that recommend_games retrieves with
RECALL_AT = (10, 25)
DIVERSITY_PICKS = 10


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
    summary: Summary
    games: list[HeldOutGame]
    top_picks: list[str]  # the full profile's picks that diversity was measured on


def weight_of(game: LibraryGame) -> float:
    return taste_weight(game["playtime_hours"], game["main_story_hours"], game["tags"] or {})


def pick_held_out(games: dict[int, LibraryGame], listings: dict[int, GameSummary], n: int) -> list[LibraryGame]:
    """The n strongest games by taste weight that the ranking could find at all.

    A game must have tags, sit in one of the full profile's tag pools and pass the shovelware floor; otherwise its
    miss measures retrieval coverage, not ranking. Ties break on appid so the set is stable between runs.
    """
    findable = [
        game
        for game in games.values()
        if game["tags"] and game["appid"] in listings and passes_floor(listings[game["appid"]])
    ]
    findable.sort(key=lambda game: (-weight_of(game), game["appid"]))
    return findable[:n]


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
) -> EvalResult:
    """Run the leave-one-out over `games` (the enriched library); `owned_appids` is every owned appid, as excluded.

    `last_played` (appid -> rtime_last_played) lets the profile count abandoned games against their tags, as live.
    """
    profile = library_profile(games.values(), df_table, last_played)
    pools, _ = rec.fetch_tag_pools(top_tags(profile, POOL_TAGS))
    listings = {int(game["appid"]): game for pool in pools.values() for game in pool.values()}
    held_out = pick_held_out(games, listings, HOLDOUT_GAMES)

    results: list[HeldOutGame] = []
    for n, game in enumerate(held_out, start=1):
        status.update(f"ranking without {game['name']}", n, len(held_out))
        rest = [other for other in games.values() if other["appid"] != game["appid"]]
        without = library_profile(rest, df_table, last_played)
        ranked = rec.recommend(without, top_tags(without, POOL_TAGS), owned_appids - {game["appid"]}, df_table)
        results.append(
            {
                "appid": game["appid"],
                "name": game["name"],
                "taste_weight": weight_of(game),
                "rank": rank_of(game["appid"], ranked["recommendations"]),
            }
        )

    status.update("ranking with the full profile, for diversity")
    picks = rec.recommend(profile, top_tags(profile, POOL_TAGS), owned_appids, df_table)["recommendations"]
    picks = picks[:DIVERSITY_PICKS]
    records = rec.enrich_tags([pick["appid"] for pick in picks])
    vectors = [tag_vector(records[pick["appid"]]["tags"], df_table) for pick in picks if pick["appid"] in records]

    return {
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summarize([result["rank"] for result in results], mean_pairwise_cosine(vectors)),
        "games": results,
        "top_picks": [pick["name"] for pick in picks],
    }


def save(result: EvalResult) -> Path:
    """Write the run to EVAL_DIR/<timestamp>.json and return the path."""
    path = EVAL_DIR / f"{result['created_at'].replace(':', '-')}.json"
    write_json(path, result)
    return path
