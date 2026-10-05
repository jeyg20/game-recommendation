"""Turn a taste profile into ranked recommendations: retrieve by top tags, narrow locally, score finalists by cosine."""

import time
from datetime import date
from typing import TypedDict, cast

from src.config import STEAM_BACKGROUND_CORPUS, TAG_POOLS_PATH
from src.steamspy import GameSummary, get_game_details, get_games_by_tag
from src.storage import append_jsonl, read_json, write_json
from src.tag_profile import (
    CorpusRecord,
    DfTable,
    TagVector,
    cosine,
    is_excluded,
    load_cache_status,
    norm,
    tag_vector,
)

TAG_POOL_MAX_AGE_DAYS = 1  # SteamSpy refreshes once a day server-side
MIN_REVIEWS = 500  # shovelware floor: positive + negative
MIN_POSITIVE_RATIO = 0.75
FINALISTS = 100


class TagPoolCache(TypedDict):
    fetched_at: str  # ISO date
    games: dict[str, GameSummary]


class Recommendation(TypedDict):
    appid: int
    name: str
    score: float  # cosine against the profile, [0, 1]
    positive_ratio: float | None  # None for backlog games (no review counts on hand)
    matched_tags: list[str]  # the candidate's strongest tags that are also strong in the profile


def fetch_tag_pools(tags: list[str]) -> dict[str, dict[str, GameSummary]]:
    """tag -> SteamSpy `tag` listing. Cached per tag for a day; fresh fetches are spaced 1 req/sec."""
    cache: dict[str, TagPoolCache] = {}
    if TAG_POOLS_PATH.is_file():
        cache = cast(dict[str, TagPoolCache], read_json(TAG_POOLS_PATH))

    pools: dict[str, dict[str, GameSummary]] = {}
    for tag in tags:
        cached = cache.get(tag)
        if (
            cached is not None
            and (date.today() - date.fromisoformat(cached["fetched_at"])).days < TAG_POOL_MAX_AGE_DAYS
        ):
            pools[tag] = cached["games"]
            continue

        print(f"Fetching SteamSpy tag pool {tag!r}")
        games = get_games_by_tag(tag)
        time.sleep(1)
        if games is None:
            print(f"Tag pool {tag!r} failed, skipping")
            continue
        if not games:
            print(f"Tag pool {tag!r} is empty (tag string not in SteamSpy's vocabulary?)")
        pools[tag] = games
        cache[tag] = {"fetched_at": date.today().isoformat(), "games": games}
        write_json(TAG_POOLS_PATH, cache)

    return pools


def positive_ratio(game: GameSummary) -> float:
    total = game["positive"] + game["negative"]
    return game["positive"] / total if total else 0.0


def passes_floor(game: GameSummary) -> bool:
    """Shovelware floor from the review counts the `tag` listing already carries."""
    return game["positive"] + game["negative"] >= MIN_REVIEWS and positive_ratio(game) >= MIN_POSITIVE_RATIO


def pick_finalists(pools: dict[str, dict[str, GameSummary]], excluded_appids: set[int]) -> list[GameSummary]:
    """Union the pools, drop excluded games and shovelware, rank by how many pools each game hit. No requests."""
    hits: dict[int, int] = {}
    games: dict[int, GameSummary] = {}
    for pool in pools.values():
        for game in pool.values():
            appid = int(game["appid"])
            if appid in excluded_appids or not passes_floor(game):
                continue
            hits[appid] = hits.get(appid, 0) + 1
            games[appid] = game

    ranked = sorted(games, key=lambda appid: (hits[appid], games[appid]["positive"]), reverse=True)
    return [games[appid] for appid in ranked[:FINALISTS]]


def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
    """appid -> corpus record with full tags. Reuses the background corpus; misses are fetched and appended to it."""
    corpus = load_cache_status(STEAM_BACKGROUND_CORPUS) if STEAM_BACKGROUND_CORPUS.is_file() else {}
    # a cached `excluded` game stays out without a refetch; a `failed` one gets another try
    missing = [appid for appid in appids if appid not in corpus or corpus[appid]["status"] == "failed"]
    if missing:
        print(f"Fetching tags for {len(missing)} finalists not in the corpus (~{len(missing)}s at 1 req/s)")

    for n, appid in enumerate(missing, start=1):
        details = get_game_details(appid)
        record: CorpusRecord = {
            "appid": appid,
            "tags": {},
            "genre": "",
            "fetched_at": date.today().isoformat(),
            "status": "failed",
        }
        if details is not None:
            record["tags"] = details["tags"]
            record["genre"] = details["genre"]
            excluded = not details["tags"] or is_excluded(details["genre"], details["tags"])
            record["status"] = "excluded" if excluded else "ok"
        corpus[appid] = record
        append_jsonl(STEAM_BACKGROUND_CORPUS, record)
        print(f"[{n}/{len(missing)}] appid {appid}: {record['status']}")
        time.sleep(1)

    return {appid: corpus[appid] for appid in appids if appid in corpus and corpus[appid]["status"] == "ok"}


def matched_tags(candidate: TagVector, profile: TagVector, n: int = 5) -> list[str]:
    """Tags contributing most to the dot product: what the candidate and the profile have in common."""
    shared = {tag: value * profile[tag] for tag, value in candidate.items() if tag in profile}
    return sorted(shared, key=shared.__getitem__, reverse=True)[:n]


def score(
    appid: int, name: str, tags: dict[str, int], profile: TagVector, profile_norm: float, df_table: DfTable
) -> Recommendation:
    vector = tag_vector(tags, df_table)
    return {
        "appid": appid,
        "name": name,
        "score": cosine(profile, profile_norm, vector),
        "positive_ratio": None,
        "matched_tags": matched_tags(vector, profile),
    }


AVOID_TOP_TAGS = 5  # a candidate is dropped when an avoided tag is among its strongest tags


def has_avoided_tag(tags: dict[str, int], avoid_tags: list[str]) -> bool:
    avoided = {tag.lower() for tag in avoid_tags}
    strongest = sorted(tags, key=tags.__getitem__, reverse=True)[:AVOID_TOP_TAGS]
    return any(tag.lower() in avoided for tag in strongest)


def recommend(
    profile: TagVector,
    top: list[str],
    excluded_appids: set[int],
    df_table: DfTable,
    avoid_tags: list[str] | None = None,
) -> list[Recommendation]:
    """Steps 5-7: retrieve pools for the top tags, narrow to finalists, score them. Best first.

    `excluded_appids` never come back (owned games, the reference games of a "more like X" request).
    """
    pools = fetch_tag_pools(top)
    finalists = pick_finalists(pools, excluded_appids)
    records = enrich_tags([int(game["appid"]) for game in finalists])
    profile_norm = norm(profile)

    recommendations: list[Recommendation] = []
    for game in finalists:
        record = records.get(int(game["appid"]))
        if record is None or (avoid_tags and has_avoided_tag(record["tags"], avoid_tags)):
            continue
        rec = score(int(game["appid"]), game["name"], record["tags"], profile, profile_norm, df_table)
        rec["positive_ratio"] = positive_ratio(game)
        recommendations.append(rec)

    recommendations.sort(key=lambda rec: rec["score"], reverse=True)
    return recommendations


def score_backlog(
    backlog: list[tuple[int, str, dict[str, int]]], profile: TagVector, df_table: DfTable
) -> list[Recommendation]:
    """Step 8: owned-but-unplayed games against the profile, from data already fetched. Best first."""
    profile_norm = norm(profile)
    scored = [score(appid, name, tags, profile, profile_norm, df_table) for appid, name, tags in backlog]
    scored.sort(key=lambda rec: rec["score"], reverse=True)
    return scored
