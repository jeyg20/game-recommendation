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
from src.storage import append_jsonl, read_json, read_jsonl, write_json


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
TOP_TAGS_CHECKED = 2


def split_genres(genre: str) -> list[str]:
    """SteamSpy's comma-separated genre string -> list, blanks dropped ("" -> [])."""
    return [g.strip() for g in genre.split(",") if g.strip()]


def is_excluded(genre: str, tags: dict[str, int]) -> bool:
    """Non-games (software genres, or tools/docs by top tags). The one rule for both the corpus and the owned library.

    Untagged games pass: SteamSpy has no tags yet for new releases. The corpus drops them separately.
    """
    genres = {g.lower() for g in split_genres(genre)}

    top_tags = sorted(tags, key=tags.__getitem__, reverse=True)[:TOP_TAGS_CHECKED]
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

    # pool order first (most-owned games first), then cached appids so due `failed` records get retried
    candidates = dict.fromkeys([*(game["appid"] for game in steam_games_collection.values()), *corpus])
    to_fetch = [appid for appid in candidates if needs_fetch(appid, corpus)]
    print(
        f"Pool: {len(steam_games_collection)} games from SteamSpy, {len(corpus)} cached, "
        f"{len(to_fetch)} to fetch (~{len(to_fetch) // 60} min at 1 req/s)"
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

            # untagged games add nothing to df, so the corpus drops them on top of is_excluded
            if not game_details["tags"] or is_excluded(game_details["genre"], game_details["tags"]):
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


def load_df_table() -> DfTable:
    """The saved df table, or build it from the corpus (and save it) if it hasn't been written yet."""
    if DF_TABLE_PATH.is_file():
        return cast(DfTable, read_json(DF_TABLE_PATH))
    if not STEAM_BACKGROUND_CORPUS.is_file():
        raise RuntimeError(f"No corpus at {STEAM_BACKGROUND_CORPUS}: run `python -m src.tag_profile` first")
    df_table = build_df_table(load_cache_status(STEAM_BACKGROUND_CORPUS))
    write_json(DF_TABLE_PATH, df_table)
    return df_table


# ---------------------------------------------------------------------
# 3. Taste weight per owned game
# ---------------------------------------------------------------------
VOLUME_SCALE_HOURS = 40
# Games where HLTB's main story is meaningless (no story to finish): only playtime volume counts.
OPEN_ENDED_TAGS = {"multiplayer", "massively multiplayer", "mmorpg", "pvp", "battle royale", "idler", "clicker"}
OPEN_ENDED_TOP_TAGS = 5


def is_open_ended(tags: dict[str, int]) -> bool:
    """True when one of the game's top tags marks it as multiplayer/idle."""
    top_tags = sorted(tags, key=tags.__getitem__, reverse=True)[:OPEN_ENDED_TOP_TAGS]
    return any(tag.lower() in OPEN_ENDED_TAGS for tag in top_tags)


def taste_weight(playtime_hours: float, main_story_hours: float | None, tags: dict[str, int]) -> float:
    """How much an owned game says about the player's taste, in [0, 1].

    completion = min(playtime / main_story, 1)   capped, no blowout
    volume     = 1 - exp(-playtime / 40)         saturating
    weight     = mean of the two, or volume only when there's no usable main story
    """
    volume = 1 - math.exp(-playtime_hours / VOLUME_SCALE_HOURS)
    if not main_story_hours or is_open_ended(tags):
        return volume
    completion = min(playtime_hours / main_story_hours, 1)
    return (completion + volume) / 2


# ---------------------------------------------------------------------
# 4. Profile vector
# ---------------------------------------------------------------------
type TagVector = dict[str, float]


def tag_vector(tags: dict[str, int], df_table: DfTable) -> TagVector:
    """A game's tags as vote shares scaled by idf. Tags missing from the corpus get the df=0 idf."""
    total_votes = sum(tags.values())
    if total_votes == 0:
        return {}
    unseen_idf = math.log(1 + df_table["n"]) + 1
    return {tag: votes / total_votes * df_table["idf"].get(tag, unseen_idf) for tag, votes in tags.items()}


def build_profile(games: list[tuple[float, dict[str, int]]], df_table: DfTable) -> TagVector:
    """Sum of weight * tag_vector over (weight, tags) pairs. The whole dict is the vector: never truncate it."""
    profile: Counter[str] = Counter()
    for weight, tags in games:
        if weight <= 0:
            continue
        for tag, value in tag_vector(tags, df_table).items():
            profile[tag] += weight * value
    return dict(profile)


def top_tags(profile: TagVector, n: int) -> list[str]:
    """The n strongest profile tags. Only for pulling the candidate pool, not for scoring."""
    return sorted(profile, key=profile.__getitem__, reverse=True)[:n]


# ---------------------------------------------------------------------
# 5. Ranking
# ---------------------------------------------------------------------
def norm(vector: TagVector) -> float:
    return math.sqrt(sum(value * value for value in vector.values()))


def cosine(profile: TagVector, profile_norm: float, candidate: TagVector) -> float:
    """dot / (|a| * |b|). The profile norm is passed in: it doesn't change across candidates.

    Both vectors must come from the SAME df table, or the axes don't line up.
    """
    candidate_norm = norm(candidate)
    if profile_norm == 0 or candidate_norm == 0:
        return 0.0
    dot = sum(value * profile.get(tag, 0.0) for tag, value in candidate.items())
    return dot / (profile_norm * candidate_norm)


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
