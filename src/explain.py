"""Why one game scored what it did: its cosine against the profile, split into per-tag shares, traced back to the
owned games that put those tags in the profile.

The score is dot / (|profile| * |candidate|), and the dot product is a sum over tags, so each tag's term divided by
the same two norms is its exact contribution: the contributions add up to the score. The library profile is
sum(weight * tag_vector(game)) with tools.library_weights' weights, so each owned game's part of a tag is its own
term of that sum. An abandoned game's term is negative: it pulled the tag down, so it's never named as a reason.
"""

from collections.abc import Iterable, Mapping
from typing import TypedDict

from src.library import LibraryGame
from src.tag_profile import DfTable, TagVector, cosine, norm, tag_vector

TOP_TAGS = 8  # tags listed with their contribution
TRACED_TAGS = 3  # of those, how many get traced back to owned games
GAMES_PER_TAG = 3  # owned games named per traced tag


class TagContribution(TypedDict):
    tag: str
    contribution: float  # this tag's part of the score; all tags' parts sum to the score
    share_of_score: float  # contribution / score


class PlayedGame(TypedDict):
    name: str
    hours: float
    tags: list[str]  # the traced tags this game is among the biggest contributors to


class Explanation(TypedDict):
    score: float
    tags: list[TagContribution]  # the TOP_TAGS biggest contributions, biggest first
    other_tags: float  # what every other shared tag adds up to: score = sum(tags) + other_tags
    because_you_played: list[PlayedGame]


def contributions(candidate: TagVector, profile: TagVector) -> dict[str, float]:
    """tag -> its exact share of cosine(profile, candidate). Only tags both vectors have."""
    denominator = norm(profile) * norm(candidate)
    if denominator == 0:
        return {}
    return {tag: value * profile[tag] / denominator for tag, value in candidate.items() if tag in profile}


def game_shares(
    tag: str, library_games: Iterable[LibraryGame], df_table: DfTable, weights: Mapping[int, float]
) -> list[tuple[LibraryGame, float]]:
    """(game, weight * tag_vector(game)[tag]) for owned games carrying the tag that added to it, biggest first.

    `weights` (appid -> weight) are the ones the profile was built with, so these are the terms the profile's value
    for the tag is the sum of. Negative terms (abandoned games) are left out.
    """
    shares: list[tuple[LibraryGame, float]] = []
    for game in library_games:
        tags = game["tags"] or {}
        if tag not in tags or game["appid"] not in weights:
            continue
        share = weights[game["appid"]] * tag_vector(tags, df_table)[tag]
        if share > 0:
            shares.append((game, share))
    shares.sort(key=lambda pair: pair[1], reverse=True)
    return shares


def explain(
    candidate_tags: dict[str, int],
    profile: TagVector,
    df_table: DfTable,
    library_games: Iterable[LibraryGame],
    weights: Mapping[int, float],
) -> Explanation:
    """The score of a game with these tags against `profile`, the tags behind it and the owned games behind those.

    `weights` are the owned games' weights in the library profile (tools.library_weights).
    """
    candidate = tag_vector(candidate_tags, df_table)
    parts = contributions(candidate, profile)
    score = cosine(profile, norm(profile), candidate)
    ranked = sorted(parts, key=parts.__getitem__, reverse=True)
    top = ranked[:TOP_TAGS]

    games = [game for game in library_games if game["tags"]]
    played: dict[int, PlayedGame] = {}  # appid -> entry, in order of first appearance
    for tag in top[:TRACED_TAGS]:
        for game, _ in game_shares(tag, games, df_table, weights)[:GAMES_PER_TAG]:
            entry = played.setdefault(
                game["appid"], {"name": game["name"], "hours": round(game["playtime_hours"], 1), "tags": []}
            )
            entry["tags"].append(tag)

    return {
        "score": score,
        "tags": [
            {"tag": tag, "contribution": parts[tag], "share_of_score": parts[tag] / score if score else 0.0}
            for tag in top
        ],
        "other_tags": sum(parts[tag] for tag in ranked[TOP_TAGS:]),
        "because_you_played": list(played.values()),
    }
