"""Collect the owned library, enrich it with SteamSpy tags and HLTB times, then recommend games from a taste profile.

Each collection step is cached under data/raw. Run from the repo root: `python main.py [--no-llm] [--top-tags N]`.
"""

import argparse
import asyncio
from typing import TypedDict

from src import ai_agent, config, hltb, steam_api, steamspy, storage
from src.recommend import Recommendation, recommend, score_backlog
from src.steam_api import OwnedGame
from src.tag_profile import build_profile, is_excluded, load_df_table, split_genres, taste_weight, top_tags

DEFAULT_TOP_TAGS = 8
SHOWN_RECOMMENDATIONS = 15
SHOWN_BACKLOG = 10


class LibraryGame(TypedDict):
    """One owned game as it moves through the pipeline; the None fields get filled by later steps."""

    appid: int
    name: str
    playtime_hours: float
    main_story_hours: float | None  # HLTB
    review_score: int | None  # HLTB
    genres: list[str] | None  # SteamSpy
    tags: dict[str, int] | None  # SteamSpy, tag -> votes


def fetch_owned_games() -> list[OwnedGame]:
    """The account's owned games from the Steam Web API."""
    print("retriving steam user games")
    owned_games = steam_api.get_owned_games(config.STEAM_ID, config.STEAM_API_KEY)
    if owned_games is None:
        raise RuntimeError("GetOwnedGames request failed (check STEAM_API_KEY / STEAM_ID)")
    if "games" not in owned_games["response"]:
        raise RuntimeError("Steam returned no games: set the profile's Game details to Public")
    print("games retrieved")
    return owned_games["response"]["games"]


def int_keys(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Re-key by int appid. A cache hit comes back from JSON keyed by str; a fresh build is already int."""
    return {int(appid): game for appid, game in games.items()}


def to_library(player_games: list[OwnedGame]) -> dict[int, LibraryGame]:
    """appid -> LibraryGame with playtime in hours; SteamSpy/HLTB fields start empty."""
    games: dict[int, LibraryGame] = {}

    for game in player_games:
        appid = game["appid"]
        games[appid] = {
            "appid": appid,
            "name": game["name"],
            "playtime_hours": game["playtime_forever"] / 60,
            "main_story_hours": None,
            "review_score": None,
            "genres": None,
            "tags": None,
        }

    return games


def add_steamspy_data(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Fill genres and tags from SteamSpy; drops games SteamSpy doesn't know and non-games (`is_excluded`)."""
    for appid in list(games.keys()):
        entry = games[appid]
        steamspy_data = steamspy.get_game_details(entry["appid"])

        if steamspy_data is None or is_excluded(steamspy_data["genre"], steamspy_data["tags"]):
            games.pop(appid)
            continue

        entry["genres"] = split_genres(steamspy_data["genre"])
        entry["tags"] = steamspy_data["tags"]

    return games


async def add_hltb_data(games: dict[int, LibraryGame]) -> dict[int, LibraryGame]:
    """Fill main story hours and review score from the best HLTB name match (None when there's no match)."""
    print("Retriving How long to beat data of the player Owned games")
    for entry in games.values():
        hltb_result = await hltb.get_game_hltb(entry["name"])
        entry["main_story_hours"] = getattr(hltb_result, "main_story", None)
        entry["review_score"] = getattr(hltb_result, "review_score", None)

    return games


def print_section(title: str, recommendations: list[Recommendation], limit: int) -> None:
    print(f"\n=== {title} ===")
    if not recommendations:
        print("(none)")
    for rank, rec in enumerate(recommendations[:limit], start=1):
        ratio = f"  {rec['positive_ratio']:.0%} positive" if rec["positive_ratio"] is not None else ""
        print(f"{rank:>2}. {rec['name']}  [{rec['score']:.2f}]{ratio}")
        print(f"    {', '.join(rec['matched_tags'])}")


async def main(use_llm: bool, top_tag_count: int) -> None:
    player_games = storage.load_or_build(config.STEAM_DATA_FILE_PATH, fetch_owned_games)
    games = int_keys(storage.load_or_build(config.STEAMSPY_DATA, lambda: add_steamspy_data(to_library(player_games))))
    games = int_keys(await storage.load_or_build_async(config.HLTB_DATA_FILE_PATH, lambda: add_hltb_data(games)))

    df_table = load_df_table()
    tagged = [game for game in games.values() if game["tags"]]
    weighted = [
        (taste_weight(g["playtime_hours"], g["main_story_hours"], g["tags"] or {}), g["tags"] or {}) for g in tagged
    ]
    profile = build_profile(weighted, df_table)
    if not profile:
        raise RuntimeError("Empty taste profile: no owned game has both playtime and tags")
    top = top_tags(profile, top_tag_count)
    print(f"\nProfile top tags: {', '.join(top)}")

    # every owned appid, including the non-games dropped from the library, so none of them come back as picks
    owned_appids = {game["appid"] for game in player_games}
    recommendations = recommend(profile, top, owned_appids, df_table)
    backlog = score_backlog(
        [(g["appid"], g["name"], g["tags"] or {}) for g in tagged if g["playtime_hours"] == 0], profile, df_table
    )

    print_section("Recommended for you", recommendations, SHOWN_RECOMMENDATIONS)
    print_section("Already in your library, never played", backlog, SHOWN_BACKLOG)

    storage.write_json(
        config.RECOMMENDATIONS_PATH,
        {"profile_top_tags": top, "recommendations": recommendations, "backlog": backlog},
    )
    print(f"\nFull results saved to {config.RECOMMENDATIONS_PATH}")

    if not use_llm:
        return
    if not config.GEMINI_API:
        print("GEMINI_API not set, skipping the LLM explanation (use --no-llm to silence this)")
        return
    most_played = [g["name"] for g in sorted(games.values(), key=lambda g: g["playtime_hours"], reverse=True)[:10]]
    explanation = ai_agent.explain(top, most_played, recommendations[:SHOWN_RECOMMENDATIONS], backlog[:SHOWN_BACKLOG])
    print("\n=== Why these picks ===")
    print(explanation or "(the model returned no text)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-llm", action="store_true", help="skip the Gemini explanation stage")
    parser.add_argument("--top-tags", type=int, default=DEFAULT_TOP_TAGS, help="profile tags used to pull candidates")
    args = parser.parse_args()
    asyncio.run(main(use_llm=not args.no_llm, top_tag_count=args.top_tags))
