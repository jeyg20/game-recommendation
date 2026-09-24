"""Build a taste profile from the owned library, weighted against a Steam-wide tag corpus."""

import json
import time
from pathlib import Path
from pprint import pprint

from steamspy import (
    GameSummary,
    get_game_details,
    get_games_by_page,
    get_top_100_games_forever,
    get_top_100_games_two_weeks,
)

# ---------------------------------------------------------------------
# 1. Background corpus (one-time, cached)
# ---------------------------------------------------------------------
# TODO: collect appids from `all` pages 0, 2, 5 (1 req/60s) + top100forever + top100in2weeks
# TODO: dedupe — the top100s are mostly already inside `all` page 0
# TODO: random-sample ~1000 appids from that pool (appdetails is 1 req/sec, don't fetch all 3000)
# TODO: appdetails each -> cache raw {appid: tags} to disk; this is the expensive step, never refetch
#       per-id cache as JSON Lines (data/appdetails.jsonl), one json.dumps(game) + "\n" per line
#       - write each game right after fetching it, file opened in append mode ("a")
#       - on startup, read line by line -> set of done appids -> skip those in the loop
#       - wrap json.loads in try/except JSONDecodeError: a crash can leave the last line half-written
#       - normalize appids to int when loading (JSON keys/values may come back as str)
#       - record failures too (None / no tags) so broken ids aren't refetched every run
# TODO: drop untagged games and Utilities genre — same filters as candidates get
# TODO: sanity check: most common corpus tags should be Indie/Action/Singleplayer/Casual.
#       If Open World Survival Craft is near the top, the sample is contaminated.


STEAM_BACKGROUND_CORPUS = Path("data/corpus/steam_bacground_corpus.json")


def build_steam_collection():

    steam_games_collection: dict[str, GameSummary] = {}

    for i in range(0, 7, 2):
        current_page = get_games_by_page(i)

        if current_page is not None:
            print(f"Adding games of page {i} to corpus...")
            steam_games_collection |= current_page

        time.sleep(60)

    top_100_forever = get_top_100_games_forever()
    top_100_last_two_weeks = get_top_100_games_two_weeks()

    if top_100_forever is not None:
        print("Addin top 100 games forever to corpus...")
        steam_games_collection |= top_100_forever

    if top_100_last_two_weeks is not None:
        print("Adding top 100 games last two weeks to corpus...")
        steam_games_collection |= top_100_last_two_weeks

    # game_id = list(steam_games_collection.keys())[0:5]
    #
    # print(len(steam_games_collection))
    # pprint(steam_games_collection[game_id[3]])

    print("Writing json file")
    STEAM_BACKGROUND_CORPUS.parent.mkdir(parents=True, exist_ok=True)

    for game in steam_games_collection.values():
        game_details = get_game_details(game["appid"])

        if game_details is not None:
            with open(STEAM_BACKGROUND_CORPUS, "a") as file:
                file.write(
                    json.dumps({game["appid"]: {"tags": game_details["tags"], "genre": game_details["genre"]}}) + "\n"
                )


# ---------------------------------------------------------------------
# 2. df table
# ---------------------------------------------------------------------
# TODO: df = how many corpus games carry each tag
# TODO: store N alongside df in the same file — IDF is wrong if they ever drift apart
# TODO: N counts tagged games only
# TODO: idf = log((1 + N) / (1 + df)) + 1   (smoothed, never zero)

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
