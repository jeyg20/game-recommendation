"""The player's Xbox / Game Pass / Xbox PC app games, matched to Steam appids so they count in the taste profile and
are never recommended.

OpenXBL gives a title history, and minutes played per title from a separate request (one each), but no last-played
date. So an Xbox game weighs what a Steam game with those hours would by volume (taste_weight, no main story), and it
never counts as abandoned or fades with time. Titles are matched to Steam by name through the store search, strictly:
a wrong match puts the wrong tags in the profile, which is worse than leaving the game out. Matches (hits and misses)
are cached for 30 days, minutes (and their absence) for 7, titles for a day.
"""

import difflib
import re
import time
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from typing import Literal, TypedDict, cast

from src import config, openxbl, steam_api
from src.openxbl import XboxTitle
from src.recommend import enrich_tags
from src.steam_api import StoreSearchItem
from src.storage import read_json, write_json
from src.tag_profile import taste_weight
from src.ui import status

TITLES_MAX_AGE = timedelta(days=1)
MATCH_MAX_AGE = timedelta(days=30)  # a miss gets asked again after this: the game may have come to Steam since
# Appearing in the history is no evidence of play: on the owner's account 112 of 159 titles are PC-only entries with 0%
# progress, and a sample of 12 had no minutes at all (catalogue entries). So minutes are asked only for titles on a
# console or with progress (46 of those 47 had minutes), and a game without minutes weighs 0.
CONSOLES = {"XboxOne", "XboxSeries"}
MINUTES_MAX_AGE = timedelta(days=7)
MINUTES_MAX_REQUESTS = 80  # per refresh, oldest first: one call can't spend OpenXBL's 150 requests/hour
MINUTES_PACE_SECONDS = 0.5
MATCH_MIN_SIMILARITY = 0.9  # normalised-name similarity a non-identical store hit needs; numbers must match too

NOT_A_GAME = re.compile(r"\b(?:demo|beta|playtest|trial)\b", re.IGNORECASE)
# Store-specific suffixes the same game carries on one store and not the other
EDITION = re.compile(
    r"\b(?:(?:complete|definitive|deluxe|ultimate|standard|enhanced|anniversary|premium|gold|game of the year|goty)"
    r" edition|for windows(?: 10)?|windows(?: 10)? edition|pc edition)\b"
)
# Trailing words of a re-release Steam lists under a new name (or a store label); exact names are still preferred,
# so an Xbox "Oblivion Remastered" picks Steam's Remastered and not the original
RERELEASE = re.compile(
    r"(?: (?:remastered|remaster|enhanced|legacy|director s cut|redux|deluxe|hd|goty|pc|game preview))+$"
)
ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}


class TitlesCache(TypedDict):
    fetched_at: str  # ISO datetime of the last successful fetch
    titles: list[XboxTitle]


class Match(TypedDict):
    appid: int | None  # None: no convincing Steam match (cached too, so it isn't looked up on every refresh)
    steam_name: str | None
    matched_at: str  # ISO date


class Minutes(TypedDict):
    minutes: int | None  # None: OpenXBL has no MinutesPlayed for the title (cached too, so it isn't asked every time)
    fetched_at: str  # ISO datetime


class XboxPlayed(TypedDict):
    name: str  # the Xbox name
    hours: float


class XboxGame(TypedDict):
    """A matched, tagged Xbox game that isn't in the Steam library: one extra entry in the taste profile."""

    appid: int
    name: str  # the Steam name
    progress: float  # 0-100
    hours: float  # 0 without minutes
    weight: float  # 0 without minutes: still matched, so never recommended
    tags: dict[str, int]


class XboxLibrary(TypedDict):
    games: list[XboxGame]  # the ones counted in the profile
    appids: list[int]  # every matched appid, Steam duplicates included: never recommended
    titles: int  # distinct games in the Xbox history (demos and apps dropped)
    matched: int
    unmatched: list[str]  # Xbox names with no Steam match
    steam_duplicates: int  # matched games the Steam library already has (their Xbox hours join Steam's)
    steam_hours: dict[int, float]  # Steam appid -> hours played on Xbox, for those duplicates that have minutes
    played: list[XboxPlayed]  # every Xbox game with minutes, matched or not, most played first
    total_hours: float
    minutes_requests: int  # minutes requests this load made (0 unless refresh_xbox asked for them)
    minutes_failed: bool  # a minutes request failed (e.g. HTTP 429): the rest wait for the next refresh
    new_matches: int  # matches found by this load's store lookups
    fetch_failed: bool  # OpenXBL didn't answer, so the cached titles were used
    fetched_at: str  # when the titles in use were fetched


def words(name: str) -> str:
    """Lowercase words only, without trademark signs or punctuation."""
    return " ".join(re.sub(r"[\W_]+", " ", re.sub(r"[™®©]", "", name.lower())).split())


def normalise(name: str) -> str:
    """words() without store-specific edition suffixes."""
    return " ".join(EDITION.sub(" ", words(name)).split())


def numbers(name: str) -> set[str]:
    """The sequel numbers in a normalised name: "forza horizon 5" and "forza horizon 4" must never match."""
    return {word for word in name.split() if word.isdigit() or word in ROMAN}


def base_name(name: str) -> str:
    """A normalised name without its trailing re-release words, for games Steam lists under a newer name."""
    return RERELEASE.sub("", name)


def closeness(xbox_name: str, steam_name: str) -> tuple[int, float] | None:
    """How convincingly two names are the same game, lower first: (0, 1) identical once normalised, (1, 1) the same
    but for a re-release suffix ("The Witcher 3: Wild Hunt" is "...Wild Hunt — Remastered" on Steam), (2, -similarity)
    near-identical with the same sequel numbers. None when they aren't the same game."""
    a, b = normalise(xbox_name), normalise(steam_name)
    if a == b:
        return 0, 1.0
    if base_name(a) == base_name(b):
        return 1, 1.0
    similarity = difflib.SequenceMatcher(None, a, b).ratio()
    if numbers(a) == numbers(b) and similarity >= MATCH_MIN_SIMILARITY:
        return 2, -similarity
    return None


def best_match(xbox_name: str, items: Iterable[StoreSearchItem]) -> StoreSearchItem | None:
    """The store app that is most convincingly the same game (see closeness), or None. Ties keep store order."""
    ranked = [(rank, item) for item in items if item["type"] == "app" and (rank := closeness(xbox_name, item["name"]))]
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


def game_titles(titles: Iterable[XboxTitle]) -> list[XboxTitle]:
    """Games only (no apps, demos, betas, playtests or trials), one per normalised name.

    The history lists some games more than once (per platform): the copies collapse into one with the highest
    progress and the smallest titleId, so the match cache keeps the same key from one fetch to the next.
    """
    kept: dict[str, XboxTitle] = {}
    for title in titles:
        key = normalise(title["name"])
        if title["type"] != "Game" or NOT_A_GAME.search(title["name"]) or not key:
            continue
        if key in kept:
            other = kept[key]
            title = XboxTitle(
                titleId=min(other["titleId"], title["titleId"]),
                name=other["name"],
                type=other["type"],
                devices=sorted(set(other["devices"]) | set(title["devices"])),
                progress=max(other["progress"], title["progress"]),
            )
        kept[key] = title
    return list(kept.values())


def is_candidate(title: XboxTitle) -> bool:
    """Worth a minutes request: played on a console, or with achievement progress (see CONSOLES)."""
    return title["progress"] > 0 or not CONSOLES.isdisjoint(title["devices"])


def copy_ids(raw: Iterable[XboxTitle], titles: Iterable[XboxTitle]) -> dict[str, list[str]]:
    """Kept titleId -> the titleIds of every copy game_titles collapsed into it. OpenXBL keeps minutes per copy, and the
    copy kept (the smallest id) is often the PC entry while the console copy has the hours (Forza Horizon 6)."""
    kept = {normalise(title["name"]): title["titleId"] for title in titles}
    ids: dict[str, set[str]] = {title_id: {title_id} for title_id in kept.values()}
    for title in raw:
        if (
            (key := normalise(title["name"])) in kept
            and title["type"] == "Game"
            and not NOT_A_GAME.search(title["name"])
        ):
            ids[kept[key]].add(title["titleId"])
    return {title_id: sorted(copies) for title_id, copies in ids.items()}


def load_minutes(title_ids: list[str], fetch: bool) -> tuple[dict[str, Minutes], int, bool]:
    """(titleId -> Minutes, requests made, a request failed). Reads the cache; with `fetch`, first asks OpenXBL for the
    `title_ids` never asked or asked over MINUTES_MAX_AGE ago, oldest first, at most MINUTES_MAX_REQUESTS.
    A failed request isn't cached and stops the round (a 429 would fail the rest too); they stay due."""
    path = config.XBOX_MINUTES_PATH
    cache = cast(dict[str, Minutes], read_json(path)) if path.is_file() else {}
    if not fetch:
        return cache, 0, False
    now = datetime.now()

    def asked_at(title_id: str) -> datetime:
        entry = cache.get(title_id)
        return datetime.fromisoformat(entry["fetched_at"]) if entry is not None else datetime.min

    due = sorted((title_id for title_id in title_ids if now - asked_at(title_id) > MINUTES_MAX_AGE), key=asked_at)
    due = due[:MINUTES_MAX_REQUESTS]
    for n, title_id in enumerate(due, start=1):
        status.update("reading Xbox hours", n, len(due))
        if n > 1:
            time.sleep(MINUTES_PACE_SECONDS)
        minutes = openxbl.get_minutes_played(title_id)
        if minutes is False:
            return cache, n, True
        cache[title_id] = {"minutes": minutes, "fetched_at": now.isoformat()}
        write_json(path, cache)  # after each request: an interruption keeps what was read
    return cache, len(due), False


def played_hours(minutes: dict[str, Minutes], copies: Iterable[str]) -> float:
    """Hours played in a game, the most any of its copies has (not the sum: copies may report the same play), 0
    without minutes (not asked yet, or OpenXBL has none)."""
    return max(((entry["minutes"] or 0) / 60 for title_id in copies if (entry := minutes.get(title_id))), default=0.0)


def is_set_up() -> bool:
    """Xbox titles have been fetched before. Without them nothing reads OpenXBL until refresh_xbox asks."""
    return config.XBOX_TITLES_PATH.is_file()


def load_titles(max_age: timedelta = TITLES_MAX_AGE) -> tuple[TitlesCache | None, bool]:
    """(titles, fetch failed). From the cache when younger than `max_age`; otherwise refetched, and on a failed
    refetch the cache of any age (OpenXBL may be down). None when there is neither."""
    cache = cast(TitlesCache, read_json(config.XBOX_TITLES_PATH)) if is_set_up() else None
    if cache is not None and datetime.now() - datetime.fromisoformat(cache["fetched_at"]) < max_age:
        return cache, False
    titles = openxbl.get_titles()
    if titles is None:
        return cache, True
    fresh: TitlesCache = {"fetched_at": datetime.now().isoformat(), "titles": titles}
    write_json(config.XBOX_TITLES_PATH, fresh)
    return fresh, False


def search(xbox_name: str) -> StoreSearchItem | None | Literal[False]:
    """The best store match for an Xbox name, None for a miss, False when the store search failed (not a miss).

    A miss is searched again under the cleaned name when it differs: the store search finds nothing for some
    store-labelled names ("Cities: Skylines II - PC Edition", "Dyson Sphere Program (Game Preview)").
    """
    cleaned = base_name(normalise(xbox_name))
    terms = [xbox_name, cleaned] if cleaned and cleaned != words(xbox_name) else [xbox_name]
    for term in terms:
        items = steam_api.try_search_store(term)
        time.sleep(1)
        if items is None:
            return False
        if (hit := best_match(xbox_name, items)) is not None:
            return hit
    return None


def match_titles(titles: list[XboxTitle]) -> tuple[dict[str, Match], int]:
    """(titleId -> Match, new matches found). Only titles never looked up, or looked up over MATCH_MAX_AGE ago, hit
    the store, one request a second; a failed request isn't cached, so the next load asks again."""
    path = config.XBOX_MATCHES_PATH
    matches = cast(dict[str, Match], read_json(path)) if path.is_file() else {}
    today = datetime.now()
    due = [
        title
        for title in titles
        if title["titleId"] not in matches
        or today - datetime.fromisoformat(matches[title["titleId"]]["matched_at"]) > MATCH_MAX_AGE
    ]
    found = 0
    for n, title in enumerate(due, start=1):
        status.update(f"matching Xbox games to Steam (~{len(due) - n}s left)", n, len(due))
        hit = search(title["name"])
        if hit is False:
            continue
        matches[title["titleId"]] = {
            "appid": hit["id"] if hit else None,
            "steam_name": hit["name"] if hit else None,
            "matched_at": today.date().isoformat(),
        }
        found += hit is not None
        write_json(path, matches)  # after each lookup: a first run is minutes long, an interruption keeps the rest
    return matches, found


def matched_appid(matches: dict[str, Match], title: XboxTitle) -> int | None:
    """The Steam appid this title matched, or None for a miss or a title not looked up yet."""
    match = matches.get(title["titleId"])
    return match["appid"] if match is not None else None


def load_xbox(
    steam_appids: Collection[int], max_age: timedelta = TITLES_MAX_AGE, fetch_minutes: bool = False
) -> XboxLibrary | None:
    """The Xbox games to count in the profile and to exclude from picks, or None when no titles were ever fetched
    and OpenXBL can't be read (no key, or a failed request). Matches new titles and fetches missing tags on the way;
    minutes played come from their cache, refreshed first only with `fetch_minutes` (refresh_xbox).
    """
    cache, fetch_failed = load_titles(max_age)
    if cache is None:
        return None
    titles = game_titles(cache["titles"])
    matches, new_matches = match_titles(titles)
    copies = copy_ids(cache["titles"], titles)
    asked = [title_id for title in titles if is_candidate(title) for title_id in copies[title["titleId"]]]
    minutes, minutes_requests, minutes_failed = load_minutes(asked, fetch_minutes)
    hours = {title["titleId"]: played_hours(minutes, copies[title["titleId"]]) for title in titles}
    matched = [(title, appid) for title in titles if (appid := matched_appid(matches, title)) is not None]
    on_xbox_only = [(title, appid) for title, appid in matched if appid not in steam_appids]
    records = enrich_tags(sorted({appid for _, appid in on_xbox_only}))
    games: dict[int, XboxGame] = {}  # two Xbox titles can match one Steam game: keep the more played
    for title, appid in on_xbox_only:
        game_hours = hours[title["titleId"]]
        if appid in records and (appid not in games or games[appid]["hours"] < game_hours):
            games[appid] = {
                "appid": appid,
                "name": matches[title["titleId"]]["steam_name"] or title["name"],
                "progress": title["progress"],
                "hours": game_hours,
                "weight": taste_weight(game_hours, None, records[appid]["tags"]) if game_hours else 0.0,
                "tags": records[appid]["tags"],
            }
    steam_hours: dict[int, float] = {}
    for title, appid in matched:
        if appid in steam_appids and hours[title["titleId"]]:
            steam_hours[appid] = max(steam_hours.get(appid, 0.0), hours[title["titleId"]])
    played: list[XboxPlayed] = [
        {"name": title["name"], "hours": round(hours[title["titleId"]], 1)}
        for title in titles
        if hours[title["titleId"]]
    ]
    played.sort(key=lambda game: game["hours"], reverse=True)
    return {
        "games": list(games.values()),
        "appids": sorted({appid for _, appid in matched}),
        "titles": len(titles),
        "matched": len(matched),
        "unmatched": [title["name"] for title in titles if matched_appid(matches, title) is None],
        "steam_duplicates": len(matched) - len(on_xbox_only),
        "steam_hours": steam_hours,
        "played": played,
        "total_hours": round(sum(hours.values()), 1),
        "minutes_requests": minutes_requests,
        "minutes_failed": minutes_failed,
        "new_matches": new_matches,
        "fetch_failed": fetch_failed,
        "fetched_at": cache["fetched_at"],
    }
