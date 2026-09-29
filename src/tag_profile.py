"""Build a taste profile from the owned library, weighted against a Steam-wide tag corpus."""

import json
import math
import time
from collections import Counter
from datetime import date
from pathlib import Path
from pprint import pprint
from typing import Literal, TypedDict

from steamspy import (
    GameSummary,
    get_game_details,
    get_games_by_page,
    get_top_100_games_forever,
    get_top_100_games_two_weeks,
)


class CorpusRecord(TypedDict):
    appid: int
    genre: str
    tags: dict[str, int]
    fetched_at: str
    status: Literal["ok", "failed", "excluded"]


STEAM_BACKGROUND_CORPUS = Path("data/corpus/steam_background_corpus.json")
DF_TABLE_PATH = Path("data/corpus/df_table.json")
RETRY_FAILED_AFTER_DAYS = 7
EXCLUDED_GENRES = {"utilities", "design & illustration", "video production", "animation & modeling"}
EXCLUDED_TAGS = {"software", "utilities", "benchmark", "game development", "documentary", "movie", "feature film"}
EMPTY_GENRE_TOP_TAGS = 2


def is_excluded(genre: str, tags: dict[str, int]) -> bool:
    """Non-games (software genres, untagged, or tools/docs) stay out of the corpus."""
    if not tags:
        return True

    genres = {g.strip().lower() for g in genre.split(",") if g.strip()}

    top_tags = sorted(tags, key=tags.__getitem__, reverse=True)[:EMPTY_GENRE_TOP_TAGS]
    return any(tag.lower() in EXCLUDED_TAGS for tag in top_tags) or bool(genres & EXCLUDED_GENRES)


def load_cache_status(path: Path) -> dict[int, CorpusRecord]:
    """appid -> CorpusRecord, one record per JSONL line."""
    corpus: dict[int, CorpusRecord] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record: CorpusRecord = json.loads(line)
            except json.JSONDecodeError:
                continue
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


# ---------------------------------------------------------------------
# 2. df table
# ---------------------------------------------------------------------
# TODO: refactor build_df_table:
#       - filter `ok` records once into a list; N = its length, df counts over the same list
#       - count df with collections.Counter (.update(record["tags"]) per ok record), loop corpus.values()
#       - use the Counter as "df" directly, build "idf" with a dict comprehension
#       - add a DfTable TypedDict (n: int, df: dict[str, int], idf: dict[str, float]) as return type
#       - keep build pure; separate save/load to data/corpus/df_table.json
#       - steps 4/5 load the saved table so profile and candidates share the same one


class DfTable(TypedDict):
    n: int
    df: dict[str, int]
    idf: dict[str, float]


def load_df_table(path: Path) -> DfTable:
    json_string = path.read_text(encoding="utf-8")
    return json.loads(json_string)


def save_df_table(table: DfTable, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=4), encoding="utf-8")


def build_df_table(corpus: dict[int, CorpusRecord]) -> DfTable:
    """Create df table

    df = how many corpus games carry each tag
    N counts tagged games only
    idf = log((1 + N) / (1 + df)) + 1   (smoothed, never zero)
    """

    ok_records = []

    for record in corpus.values():
        if record["status"] == "ok":
            ok_records.append(record)

    n = len(ok_records)

    counter = Counter()
    for record in ok_records:
        counter.update(record["tags"].keys())

    df_table: DfTable = {
        "n": n,
        "df": dict(counter),
        "idf": {tag: math.log((1 + n) / (1 + df_value)) + 1 for tag, df_value in counter.items()},
    }

    return df_table


def reclassify_cache(corpus: dict[int, CorpusRecord]):
    for item in corpus:
        if is_excluded(corpus[item]["genre"], corpus[item]["tags"]):
            pass


def build_steam_collection():
    steam_games_collection: dict[str, GameSummary] = {}
    corpus: dict[int, CorpusRecord] = {}
    STEAM_BACKGROUND_CORPUS.parent.mkdir(parents=True, exist_ok=True)

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
        with open(STEAM_BACKGROUND_CORPUS, "a", encoding="utf-8") as file:
            file.write(json.dumps(game_corpus) + "\n")
        print(f"[{n}/{len(to_fetch)}] appid {game_id}: {game_corpus['status']} ({len(game_corpus['tags'])} tags)")
        time.sleep(1)

    print(f"Done. Corpus has {len(corpus)} records.")

    df_table = build_df_table(corpus)

    save_df_table(df_table, DF_TABLE_PATH)


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


def test_func():
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
