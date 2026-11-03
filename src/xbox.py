"""The player's Xbox / Game Pass / Xbox PC app games, matched to Steam appids so they count in the taste profile and
are never recommended.

OpenXBL gives a title history with achievement progress but no hours and no last-played date, so an Xbox game's
weight comes from progress alone, and it never counts as abandoned or fades with time. Titles are matched to Steam
by name through the store search, strictly: a wrong match puts the wrong tags in the profile, which is worse than
leaving the game out. Matches (hits and misses) are cached for 30 days, titles for a day.
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
from src.ui import status

TITLES_MAX_AGE = timedelta(days=1)
MATCH_MAX_AGE = timedelta(days=30)  # a miss gets asked again after this: the game may have come to Steam since
# Appearing in the history is weak evidence: on the owner's account 64 of 88 counted games have 0% progress (store and
# catalogue entries among them), and at 0.2 each they outweighed the whole Steam library (18.4 vs 16.4 total weight).
# So presence counts a little and progress is the signal: 0.05-0.65, against up to 1.0 for a Steam game played through.
XBOX_BASE_WEIGHT = 0.05
XBOX_PROGRESS_WEIGHT = 0.6  # times achievement progress (0-1)
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


class XboxGame(TypedDict):
    """A matched, tagged Xbox game that isn't in the Steam library: one extra entry in the taste profile."""

    appid: int
    name: str  # the Steam name
    progress: float  # 0-100
    weight: float
    tags: dict[str, int]


class XboxLibrary(TypedDict):
    games: list[XboxGame]  # the ones counted in the profile
    appids: list[int]  # every matched appid, Steam duplicates included: never recommended
    titles: int  # distinct games in the Xbox history (demos and apps dropped)
    matched: int
    unmatched: list[str]  # Xbox names with no Steam match
    steam_duplicates: int  # matched games the Steam library already has (Steam's hours count instead)
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


def xbox_weight(progress: float) -> float:
    """XBOX_BASE_WEIGHT for being in the history, plus XBOX_PROGRESS_WEIGHT times the share of achievements."""
    return XBOX_BASE_WEIGHT + XBOX_PROGRESS_WEIGHT * min(max(progress, 0.0), 100.0) / 100


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


def load_xbox(steam_appids: Collection[int], max_age: timedelta = TITLES_MAX_AGE) -> XboxLibrary | None:
    """The Xbox games to count in the profile and to exclude from picks, or None when no titles were ever fetched
    and OpenXBL can't be read (no key, or a failed request). Matches new titles and fetches missing tags on the way.
    """
    cache, fetch_failed = load_titles(max_age)
    if cache is None:
        return None
    titles = game_titles(cache["titles"])
    matches, new_matches = match_titles(titles)
    matched = [(title, appid) for title in titles if (appid := matched_appid(matches, title)) is not None]
    on_xbox_only = [(title, appid) for title, appid in matched if appid not in steam_appids]
    records = enrich_tags(sorted({appid for _, appid in on_xbox_only}))
    games: dict[int, XboxGame] = {}  # two Xbox titles can match one Steam game: keep the stronger
    for title, appid in on_xbox_only:
        if appid in records and (appid not in games or games[appid]["progress"] < title["progress"]):
            games[appid] = {
                "appid": appid,
                "name": matches[title["titleId"]]["steam_name"] or title["name"],
                "progress": title["progress"],
                "weight": xbox_weight(title["progress"]),
                "tags": records[appid]["tags"],
            }
    return {
        "games": list(games.values()),
        "appids": sorted({appid for _, appid in matched}),
        "titles": len(titles),
        "matched": len(matched),
        "unmatched": [title["name"] for title in titles if matched_appid(matches, title) is None],
        "steam_duplicates": len(matched) - len(on_xbox_only),
        "new_matches": new_matches,
        "fetch_failed": fetch_failed,
        "fetched_at": cache["fetched_at"],
    }
