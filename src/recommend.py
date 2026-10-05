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
from src.ui import status, style

TAG_POOL_MAX_AGE_DAYS = 1  # SteamSpy refreshes once a day server-side
MIN_REVIEWS = 500  # shovelware floor: positive + negative
MIN_POSITIVE_RATIO = 0.75
FINALISTS = 100
MIN_STRICT_RESULTS = 10  # fewer picks than this carry every required tag -> accept any one of them instead


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
    for n, tag in enumerate(tags, start=1):
        cached = cache.get(tag)
        if (
            cached is not None
            and (date.today() - date.fromisoformat(cached["fetched_at"])).days < TAG_POOL_MAX_AGE_DAYS
        ):
            pools[tag] = cached["games"]
            continue

        status.update(f"fetching games tagged {tag!r}", n, len(tags))
        games = get_games_by_tag(tag)
        time.sleep(1)
        if games is None:
            status.say(style(f"  ! SteamSpy tag pool {tag!r} failed, skipping it", "yellow"))
            continue
        if not games:  # not cached: an empty listing is a miss, and caching it would repeat it all day
            status.say(style(f"  ! tag pool {tag!r} is empty (not in SteamSpy's vocabulary?)", "yellow"))
            continue
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


def pick_finalists(
    pools: dict[str, dict[str, GameSummary]], excluded_appids: set[int], allowed_appids: set[int] | None = None
) -> list[GameSummary]:
    """Union the pools, drop excluded games and shovelware, rank by how many pools each game hit. No requests.

    `allowed_appids`, when given, is the only set a finalist can come from (the required-tag filter).
    """
    hits: dict[int, int] = {}
    games: dict[int, GameSummary] = {}
    for pool in pools.values():
        for game in pool.values():
            appid = int(game["appid"])
            if appid in excluded_appids or not passes_floor(game):
                continue
            if allowed_appids is not None and appid not in allowed_appids:
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
    for n, appid in enumerate(missing, start=1):
        status.update(f"fetching tags for new candidates (~{len(missing) - n}s left)", n, len(missing))
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


REQUIRED_TOP_TAGS = 10  # a required tag must be this prominent: SteamSpy pools also hold games where it's a footnote


def has_required_tags(tags: dict[str, int], required_tags: list[str], relaxed: bool) -> bool:
    """All required tags among the game's strongest (any one of them when the requirement was relaxed)."""
    strongest = {tag.lower() for tag in sorted(tags, key=tags.__getitem__, reverse=True)[:REQUIRED_TOP_TAGS]}
    present = [tag.lower() in strongest for tag in required_tags]
    return any(present) if relaxed else all(present)


def fits_tags(tags: dict[str, int], required_tags: list[str], avoid_tags: list[str], relaxed: bool) -> bool:
    """The required/avoided tag rules, shared by candidates and the backlog."""
    if avoid_tags and has_avoided_tag(tags, avoid_tags):
        return False
    return not required_tags or has_required_tags(tags, required_tags, relaxed)


def has_avoided_tag(tags: dict[str, int], avoid_tags: list[str]) -> bool:
    avoided = {tag.lower() for tag in avoid_tags}
    strongest = sorted(tags, key=tags.__getitem__, reverse=True)[:AVOID_TOP_TAGS]
    return any(tag.lower() in avoided for tag in strongest)


class RecommendResult(TypedDict):
    recommendations: list[Recommendation]  # best first
    pool_tags: list[str]  # the tag pools candidates were drawn from
    requirement_relaxed: bool  # too few games had every required tag, so any one of them was enough
    requirement_unenforced: list[str]  # required tags whose pool failed to fetch or came back empty


def recommend(
    profile: TagVector,
    top: list[str],
    excluded_appids: set[int],
    df_table: DfTable,
    avoid_tags: list[str] | None = None,
    required_tags: list[str] | None = None,
) -> RecommendResult:
    """Steps 5-7: retrieve pools for the top tags, narrow to finalists, score them.

    `required_tags` get their pools fetched first; every pick must be in all of them and carry each among its
    strongest tags (any one of them, when fewer than MIN_STRICT_RESULTS picks do). Without this a specific ask like
    "shooters" loses to whatever the library profile is heavy in: finalists rank by pool hits, and the profile's own
    tags win those. `excluded_appids` never come back (owned games, the reference games of a "more like X" request).
    """
    required = required_tags or []
    pool_tags = [*required, *(tag for tag in top if tag not in required)][: max(len(top), len(required) + 4)]
    pools = fetch_tag_pools(pool_tags)
    enforced = [tag for tag in required if tag in pools]  # a failed or empty pool can't filter anything
    profile_norm = norm(profile)

    def ranked(allowed_appids: set[int] | None, relaxed: bool) -> list[Recommendation]:
        finalists = pick_finalists(pools, excluded_appids, allowed_appids)
        records = enrich_tags([int(game["appid"]) for game in finalists])
        picks: list[Recommendation] = []
        for game in finalists:
            record = records.get(int(game["appid"]))
            if record is None or not fits_tags(record["tags"], enforced, avoid_tags or [], relaxed):
                continue
            rec = score(int(game["appid"]), game["name"], record["tags"], profile, profile_norm, df_table)
            rec["positive_ratio"] = positive_ratio(game)
            picks.append(rec)
        picks.sort(key=lambda rec: rec["score"], reverse=True)
        return picks

    relaxed = False
    if enforced:
        required_pools = [{int(game["appid"]) for game in pools[tag].values()} for tag in enforced]
        recommendations = ranked(set.intersection(*required_pools), relaxed=False)
        # judged on the final picks, after the strongest-tags check, not on raw pool overlap
        if len(recommendations) < MIN_STRICT_RESULTS and len(enforced) > 1:
            recommendations = ranked(set.union(*required_pools), relaxed=True)
            relaxed = True
    else:
        recommendations = ranked(None, relaxed=False)

    return {
        "recommendations": recommendations,
        "pool_tags": pool_tags,
        "requirement_relaxed": relaxed,
        "requirement_unenforced": [tag for tag in required if tag not in enforced],
    }


def score_backlog(
    backlog: list[tuple[int, str, dict[str, int]]], profile: TagVector, df_table: DfTable
) -> list[Recommendation]:
    """Step 8: owned-but-unplayed games against the profile, from data already fetched. Best first."""
    profile_norm = norm(profile)
    scored = [score(appid, name, tags, profile, profile_norm, df_table) for appid, name, tags in backlog]
    scored.sort(key=lambda rec: rec["score"], reverse=True)
    return scored
