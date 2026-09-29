"""Build a taste profile from the owned library, weighted against a Steam-wide tag corpus."""

import math
import time
from collections import Counter
from datetime import date
from pathlib import Path
from pprint import pprint
from typing import Literal, TypedDict, cast

from src.config import DF_TABLE_PATH, STEAM_BACKGROUND_CORPUS
from src.steamspy import (
    GameSummary,
    get_game_details,
    get_games_by_page,
    get_top_100_games_forever,
    get_top_100_games_two_weeks,
)
from src.storage import append_jsonl, read_jsonl, write_json


class CorpusRecord(TypedDict):
    """One line of the corpus JSONL: a fetch attempt for one app. The last line per appid wins."""

    appid: int
    genre: str
    tags: dict[str, int]
    fetched_at: str  # ISO date, drives the failed-record retry window
    status: Literal["ok", "failed", "excluded"]


RETRY_FAILED_AFTER_DAYS = 7
EXCLUDED_GENRES = {"utilities", "design & illustration", "video production", "animation & modeling"}
EXCLUDED_TAGS = {"software", "utilities", "benchmark", "game development", "documentary", "movie", "feature film"}
EMPTY_GENRE_TOP_TAGS = 2


def split_genres(genre: str) -> list[str]:
    """SteamSpy's comma-separated genre string -> list, blanks dropped ("" -> [])."""
    return [g.strip() for g in genre.split(",") if g.strip()]


def is_excluded(genre: str, tags: dict[str, int]) -> bool:
    """Non-games (software genres, untagged, or tools/docs). The one rule for both the corpus and the owned library."""
    if not tags:
        return True

    genres = {g.lower() for g in split_genres(genre)}

    top_tags = sorted(tags, key=tags.__getitem__, reverse=True)[:EMPTY_GENRE_TOP_TAGS]
    return any(tag.lower() in EXCLUDED_TAGS for tag in top_tags) or bool(genres & EXCLUDED_GENRES)


def load_cache_status(path: Path) -> dict[int, CorpusRecord]:
    """appid -> CorpusRecord, one record per JSONL line."""
    corpus: dict[int, CorpusRecord] = {}
    for raw in read_jsonl(path):
        record = cast(CorpusRecord, raw)
        corpus[int(record["appid"])] = record
    return corpus


def needs_fetch(appid: int, corpus: dict[int, CorpusRecord]) -> bool:
    """Fetch if never cached, or if it failed and the retry window has passed. `ok` records are final."""
    record = corpus.get(appid)
    if record is None:
        return True
    if record["status"] == "ok":
        return False
    if record["status"] == "excluded":
        return False
    age = date.today() - date.fromisoformat(record["fetched_at"])
    return age.days >= RETRY_FAILED_AFTER_DAYS


class DfTable(TypedDict):
    """Document frequency per tag over the `ok` corpus; saved to DF_TABLE_PATH."""

    n: int
    df: dict[str, int]
    idf: dict[str, float]


def build_df_table(corpus: dict[int, CorpusRecord]) -> DfTable:
    """Create df table

    df = how many corpus games carry each tag
    N counts tagged games only
    idf = log((1 + N) / (1 + df)) + 1   (smoothed, never zero)
    """

    ok_records: list[CorpusRecord] = []

    for record in corpus.values():
        if record["status"] == "ok":
            ok_records.append(record)

    n = len(ok_records)

    counter: Counter[str] = Counter()
    for record in ok_records:
        counter.update(record["tags"].keys())

    df_table: DfTable = {
        "n": n,
        "df": dict(counter),
        "idf": {tag: math.log((1 + n) / (1 + df_value)) + 1 for tag, df_value in counter.items()},
    }

    return df_table


def reclassify_cache(corpus: dict[int, CorpusRecord]) -> None:
    """WIP: re-run `is_excluded` over cached records after the exclusion rules change."""
    for item in corpus:
        if is_excluded(corpus[item]["genre"], corpus[item]["tags"]):
            pass


def build_steam_collection() -> None:
    """Grow the background corpus from SteamSpy (asks before paging), fetch tags for new appids, save the df table.

    Every fetch is appended to the corpus JSONL as it happens, so an interrupted run resumes where it stopped.
    """
    steam_games_collection: dict[str, GameSummary] = {}
    corpus: dict[int, CorpusRecord] = {}

    if STEAM_BACKGROUND_CORPUS.is_file():
        corpus = load_cache_status(STEAM_BACKGROUND_CORPUS)
        print(f"Loaded {len(corpus)} cached records from {STEAM_BACKGROUND_CORPUS}")
    else:
        print(f"No cache at {STEAM_BACKGROUND_CORPUS}, starting fresh")

    fetch_input = input("Do you want to fetch the pages from steamspy: (yes/no)").lower().strip()

    if fetch_input == "yes":
        print("Fetching `all` pages 0, 2, 4, 6 (1 req/60s, ~3 min)...")
        for i in range(0, 7, 2):
            current_page = get_games_by_page(i)

            if current_page is not None:
                print(f"Adding {len(current_page)} games of page {i} to corpus...")
                steam_games_collection |= current_page
            else:
                print(f"Page {i} failed, skipping")

            if i != 6:
                print("Waiting 60s for the `all` rate limit...")
                time.sleep(60)

        top_100_forever = get_top_100_games_forever()
        top_100_last_two_weeks = get_top_100_games_two_weeks()

        if top_100_forever is not None:
            print("Adding top 100 games forever to corpus...")
            steam_games_collection |= top_100_forever
        else:
            print("Top 100 forever failed, skipping")

        if top_100_last_two_weeks is not None:
            print("Adding top 100 games last two weeks to corpus...")
            steam_games_collection |= top_100_last_two_weeks
        else:
            print("Top 100 last two weeks failed, skipping")

    to_fetch = [game["appid"] for game in steam_games_collection.values() if needs_fetch(game["appid"], corpus)]
    print(
        f"Pool: {len(steam_games_collection)} games, {len(to_fetch)} to fetch, "
        f"{len(steam_games_collection) - len(to_fetch)} already cached (~{len(to_fetch) // 60} min at 1 req/s)"
    )

    for n, game_id in enumerate(to_fetch, start=1):
        game_details = get_game_details(game_id)

        game_corpus: CorpusRecord = {
            "appid": game_id,
            "tags": {},
            "genre": "",
            "fetched_at": date.today().isoformat(),
            "status": "failed",
        }
        if game_details is not None:
            game_corpus["tags"] = game_details["tags"]
            game_corpus["genre"] = game_details["genre"]

            if is_excluded(game_details["genre"], game_details["tags"]):
                game_corpus["status"] = "excluded"
            else:
                game_corpus["status"] = "ok"

        corpus[game_id] = game_corpus
        append_jsonl(STEAM_BACKGROUND_CORPUS, game_corpus)
        print(f"[{n}/{len(to_fetch)}] appid {game_id}: {game_corpus['status']} ({len(game_corpus['tags'])} tags)")
        time.sleep(1)

    print(f"Done. Corpus has {len(corpus)} records.")

    df_table = build_df_table(corpus)

    write_json(DF_TABLE_PATH, df_table)


# ---------------------------------------------------------------------
# 3. Taste weight per owned game
# ---------------------------------------------------------------------
# TODO: completion = min(playtime_hours / main_story_hours, 1)   capped, no blowout
# TODO: volume = 1 - exp(-playtime_hours / 40)                   saturating
# TODO: weight = mean of the two
# TODO: no main_story, or multiplayer/idle (HLTB denominator is junk there) -> volume only
# TODO: later — negative weight for early abandonment instead of a positive floor

# ---------------------------------------------------------------------
# 4. Profile vector
# ---------------------------------------------------------------------
# TODO: per game, normalize tag votes to proportions (share of that game's total votes)
# TODO: profile[tag] += weight * share * idf
# TODO: keep the whole dict — do NOT truncate to top-N, that dict is the vector
# TODO: top-N is only for pulling the candidate pool, not for scoring

# ---------------------------------------------------------------------
# 5. Later — ranking
# ---------------------------------------------------------------------
# TODO: cosine(a, b) = dot / (|a| * |b|)
# TODO: cache the profile's norm, it doesn't change across candidates
# TODO: candidate vectors must use the SAME df table, or the axes don't line up

# ---------------------------------------------------------------------
# Blocker
# ---------------------------------------------------------------------
# TODO: normalize appid int/str at the cache boundary before any of this —
#       subtracting owned appids from candidates fails silently otherwise


def test_func() -> None:
    """Scratch check of the `all` page shape."""
    games_dict = get_games_by_page(0)

    if games_dict is None:
        print("Pages requested doesn't exist")
    else:
        print(type(games_dict).__name__, len(games_dict))

        games_list = list(games_dict.keys())[:5]

        print(games_list)
        print(type(games_list[0]).__name__)

        pprint(games_dict[games_list[0]])


if __name__ == "__main__":
    build_steam_collection()
    # cache_data = load_cache_status(STEAM_BACKGROUND_CORPUS)
    # items_list = list(cache_data.items())
    #
    # # Grab items by index (remember Python starts counting at 0)
    # item_1 = items_list[1]  # Index 1 (The 2nd item: '1172470')
    # item_4 = items_list[4]  #
    # print(item_1)
    #
    # print(item_4)
