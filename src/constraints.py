"""Hard limits on a request: "nothing over $20", "under 10 hours". Filters the ranked picks, never the ranking itself.

Price comes with every pick (F04). Length needs HowLongToBeat, which only the owned library has, so candidates are
looked up after scoring, only for the few best picks, and cached by appid.
"""

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TypedDict, cast

from src import config, hltb, storage
from src.recommend import Recommendation
from src.ui import status

LENGTH_LOOKUP_FACTOR = 3  # look up HLTB for this many times `limit` picks: some won't fit, some have no time
HLTB_MAX_AGE_DAYS = 30  # main story times barely move; a miss may get an entry later
SECONDS_PER_DAY = 86_400

type Hours = dict[int, float | None]  # appid -> HLTB main story hours; None = HLTB has no time for it


class HltbCacheEntry(TypedDict):
    hours: float | None
    fetched_at: float  # unix time


def known_hours(value: object) -> float | None:
    """HLTB reports 0 when it has no main story time: that's unknown, not a zero-hour game."""
    return float(value) if isinstance(value, int | float) and value > 0 else None


async def hltb_main_story(name: str) -> float | None:
    """Main story hours of the best HLTB name match (None when there's no match). Raises hltb.HltbSearchFailed when
    the search failed, so it isn't cached as a miss."""
    return known_hours(getattr(await hltb.get_game_hltb(name, raise_on_failure=True), "main_story", None))


def within_price[R: Recommendation](picks: Sequence[R], max_price_usd: float | None) -> list[R]:
    """Picks the player can buy now for at most `max_price_usd` (price after any discount). Unknown prices are dropped:
    they can't be shown to fit."""
    if max_price_usd is None:
        return list(picks)
    return [pick for pick in picks if pick["price_usd"] is not None and pick["price_usd"] <= max_price_usd]


def candidate_hours(picks: Sequence[Recommendation]) -> Hours:
    """HLTB main story hours for `picks`, from the cache or looked up (one at a time, like the library) and cached.
    A failed lookup counts as unknown this time and isn't cached."""
    raw = storage.read_json(config.CANDIDATE_HLTB_PATH) if config.CANDIDATE_HLTB_PATH.is_file() else {}
    cache = cast(dict[str, HltbCacheEntry], raw)
    now = time.time()
    stale = [
        p
        for p in picks
        if now - cache.get(str(p["appid"]), {"fetched_at": 0.0})["fetched_at"] > HLTB_MAX_AGE_DAYS * SECONDS_PER_DAY
    ]

    async def fetch() -> None:
        for n, pick in enumerate(stale, start=1):
            status.update(f"fetching HowLongToBeat: {pick['name']}", n, len(stale))
            try:
                hours = await hltb_main_story(pick["name"])
            except hltb.HltbSearchFailed:
                continue  # not cached (an old entry stays stale): the next call retries it
            cache[str(pick["appid"])] = {"hours": hours, "fetched_at": now}

    if stale:
        asyncio.run(fetch())
        storage.write_json(config.CANDIDATE_HLTB_PATH, cache)
    return {p["appid"]: entry["hours"] if (entry := cache.get(str(p["appid"]))) else None for p in picks}


def within_length[R: Recommendation](
    picks: Sequence[R], limit: int, max_hours: float | None, hours_of: Callable[[Sequence[R]], Hours]
) -> tuple[list[R], Hours]:
    """The best picks known to fit `max_hours`, then the ones with no HLTB time (kept: most indie games have none).

    Only the best `LENGTH_LOOKUP_FACTOR * limit` picks are looked up; the rest are dropped unchecked. Returns the picks
    and the hours that were looked up, for `with_hours`.
    """
    if max_hours is None:
        return list(picks), {}
    head = picks[: LENGTH_LOOKUP_FACTOR * limit]
    hours = hours_of(head)
    fits = [p for p in head if (h := hours.get(p["appid"])) is not None and h <= max_hours]
    unknown = [p for p in head if hours.get(p["appid"]) is None]
    return fits + unknown, hours


def with_hours(picks: list[dict[str, object]], hours: Mapping[int, float | None]) -> list[dict[str, object]]:
    """Add `main_story_hours`, or `length_unknown`, to compacted picks that had their length checked."""
    for pick in picks:
        appid = cast(int, pick["appid"])
        if appid not in hours:
            continue
        found = hours[appid]
        pick |= {"main_story_hours": round(found, 1)} if found is not None else {"length_unknown": True}
    return picks
