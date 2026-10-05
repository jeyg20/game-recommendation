"""The functions the assistant can call. Each returns JSON-serializable data for the model.

Signatures and docstrings are turned into Gemini function declarations, so the `Args:` sections are what the model
reads: keep them accurate. Library, df table and profile are loaded once and reused until a refresh tool resets them.
"""

import difflib
from typing import TypedDict, cast

from src import library, steam_api, steamspy
from src.history import list_sessions, load_session
from src.library import LibraryGame
from src.preferences import Preferences, load_preferences, merge, save_preferences, without
from src.recommend import Recommendation, fits_tags, recommend, score, score_backlog
from src.steam_api import OwnedGame
from src.tag_profile import (
    DfTable,
    TagVector,
    adjust_profile,
    build_profile,
    build_steam_collection,
    load_df_table,
    norm,
    tag_vector,
    taste_weight,
    top_tags,
)


class ReferenceGame(TypedDict):
    appid: int
    name: str
    tags: dict[str, int]


class RequestDraft(TypedDict):
    """What the player has said they want this session, gathered over several messages before asking for picks."""

    like_games: list[str]
    boost_tags: list[str]
    avoid_tags: list[str]
    notes: list[str]  # anything the ranking can't use directly ("short sessions", "to play with my partner")


def empty_draft() -> RequestDraft:
    return {"like_games": [], "boost_tags": [], "avoid_tags": [], "notes": []}


class State(TypedDict):
    player_games: list[OwnedGame] | None
    games: dict[int, LibraryGame] | None
    df_table: DfTable | None
    profile: TagVector | None
    session: str  # set by the Assistant: the session being logged
    new_session_requested: bool  # set by start_new_session, acted on by the Assistant once the turn ends
    draft: RequestDraft  # reset with every new session


state: State = {
    "player_games": None,
    "games": None,
    "df_table": None,
    "profile": None,
    "session": "",
    "new_session_requested": False,
    "draft": empty_draft(),
}
reference_cache: dict[str, ReferenceGame | None] = {}  # lowercased name -> best match


def ensure_loaded() -> tuple[list[OwnedGame], dict[int, LibraryGame], DfTable, TagVector]:
    """Load the cached library, df table and library profile on first use."""
    if state["player_games"] is None or state["games"] is None:
        state["player_games"], state["games"] = library.load_library()
    if state["df_table"] is None:
        state["df_table"] = load_df_table()
    if state["profile"] is None:
        weighted = [
            (taste_weight(g["playtime_hours"], g["main_story_hours"], g["tags"] or {}), g["tags"] or {})
            for g in state["games"].values()
            if g["tags"]
        ]
        state["profile"] = build_profile(weighted, state["df_table"])
        if not state["profile"]:
            raise RuntimeError("Empty taste profile: no owned game has both playtime and tags")
    return state["player_games"], state["games"], state["df_table"], state["profile"]


def rounded(profile: TagVector, tags: list[str]) -> dict[str, float]:
    """tag -> weight as a share of the profile's length, for showing the model."""
    profile_norm = norm(profile) or 1.0
    return {tag: round(profile[tag] / profile_norm, 3) for tag in tags}


def canonical_tags(tags: list[str] | None, df_table: DfTable) -> tuple[list[str], dict[str, list[str]]]:
    """(tags spelled as SteamSpy spells them, unknown tag -> close matches). SteamSpy silently ignores bad spellings."""
    vocabulary = {tag.lower(): tag for tag in df_table["df"]}
    known: list[str] = []
    unknown: dict[str, list[str]] = {}
    for tag in tags or []:
        match = vocabulary.get(tag.strip().lower())
        if match is not None:
            known.append(match)
        else:
            unknown[tag] = difflib.get_close_matches(tag, list(df_table["df"]), n=3, cutoff=0.6)
    return known, unknown


def find_game(name: str) -> ReferenceGame | None:
    """Best store match for a game name, with its SteamSpy tags. Cached for the session (1-2 requests each)."""
    key = name.strip().lower()
    if key not in reference_cache:
        reference_cache[key] = None
        apps = [item for item in steam_api.search_store(name) if item["type"] == "app"]
        if apps:
            details = steamspy.get_game_details(apps[0]["id"])
            if details is not None and details["tags"]:
                reference_cache[key] = {"appid": apps[0]["id"], "name": apps[0]["name"], "tags": details["tags"]}
    return reference_cache[key]


BACKLOG_MAX_HOURS = 2.0  # "barely started": under this many hours...
BACKLOG_MAX_STORY_SHARE = 0.25  # ...and under this share of the HLTB main story, when HLTB has one


def is_backlog(game: LibraryGame) -> bool:
    """Never launched, or tried briefly and left. Short games that were actually finished don't count."""
    hours = game["playtime_hours"]
    if hours == 0:
        return True
    if hours >= BACKLOG_MAX_HOURS:
        return False
    story = game["main_story_hours"]
    return not story or hours < BACKLOG_MAX_STORY_SHARE * story


def compact(recommendations: list[Recommendation], limit: int) -> list[dict[str, object]]:
    return [
        {
            "name": rec["name"],
            "appid": rec["appid"],
            "score": round(rec["score"], 3),
            "positive_ratio": round(rec["positive_ratio"], 2) if rec["positive_ratio"] is not None else None,
            "matched_tags": rec["matched_tags"],
        }
        for rec in recommendations[:limit]
    ]


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------
def get_taste_profile(top_n: int = 15) -> dict[str, object]:
    """The player's taste profile built from their Steam library playtime, plus their saved preferences.

    Args:
      top_n: How many of the strongest profile tags to return.
    """
    _, games, _, profile = ensure_loaded()
    played = sorted(games.values(), key=lambda g: g["playtime_hours"], reverse=True)
    return {
        "top_tags": rounded(profile, top_tags(profile, top_n)),
        "most_played": [{"name": g["name"], "hours": round(g["playtime_hours"], 1)} for g in played[:10]],
        "backlog_games": sum(1 for g in games.values() if is_backlog(g)),
        "library_size": len(games),
        "saved_preferences": load_preferences(),
    }


def recommend_games(
    like_games: list[str] | None = None,
    boost_tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    limit: int = 10,
    include_backlog: bool = True,
    use_saved_preferences: bool = True,
) -> dict[str, object]:
    """Rank games the player doesn't own against their taste profile, optionally steered for this request.

    Everything recorded with note_request this session is applied automatically, so only pass what is new.

    Candidates come from SteamSpy (popular, well-reviewed games). Each result has a cosine score in [0, 1] and the
    tags it shares with the profile, which is the reason it was picked. New tag pools and game tags are fetched at
    ~1 request/second, so a first run with new tags can take a minute or two.

    Args:
      like_games: Game names to steer toward ("more like Hollow Knight and Celeste"). Each is looked up on Steam.
      boost_tags: Steam user tags the picks must have, e.g. ["Shooter"] or ["Roguelike", "Co-op"] (all of them when
        enough games qualify), and that weigh more in the ranking. Must be real Steam tag names.
      avoid_tags: Steam user tags to steer away from; games with one among their strongest tags are dropped.
      limit: How many recommendations (and backlog games) to return.
      include_backlog: Also rank owned games the player never launched or only tried briefly.
      use_saved_preferences: Apply the player's saved preferences on top of this request's.
    """
    player_games, games, df_table, profile = ensure_loaded()
    saved: Preferences = (
        load_preferences() if use_saved_preferences else {"like_games": [], "boost_tags": [], "avoid_tags": []}
    )

    draft = state["draft"]

    # this request's boosts are must-haves; saved ones are standing tastes, so they only add weight
    required, unknown_required = canonical_tags(merge(draft["boost_tags"], boost_tags), df_table)
    soft_boost, unknown_soft = canonical_tags(saved["boost_tags"], df_table)
    boost = merge(soft_boost, required)
    unknown_boost = unknown_soft | unknown_required
    avoid, unknown_avoid = canonical_tags(merge(merge(saved["avoid_tags"], draft["avoid_tags"]), avoid_tags), df_table)
    references: list[ReferenceGame] = []
    not_found: list[str] = []
    for name in merge(merge(saved["like_games"], draft["like_games"]), like_games):
        found = find_game(name)
        if found is None:
            not_found.append(name)
        else:
            references.append(found)

    steered = bool(references or boost or avoid)
    taste = (
        adjust_profile(profile, [tag_vector(ref["tags"], df_table) for ref in references], boost, avoid)
        if steered
        else profile
    )
    excluded = {game["appid"] for game in player_games} | {ref["appid"] for ref in references}
    ranked = recommend(taste, top_tags(taste, 8), excluded, df_table, avoid, required_tags=required)
    recommendations = ranked["recommendations"]

    result: dict[str, object] = {
        "applied": {
            "like_games": [ref["name"] for ref in references],
            "boost_tags": required,
            "saved_boost_tags": soft_boost,
            "avoid_tags": avoid,
            "from_saved_preferences": use_saved_preferences,
            "player_notes": draft["notes"],
        },
        "steered_top_tags": rounded(taste, top_tags(taste, 10)),
        "candidate_pools": ranked["pool_tags"],
        "recommendations": compact(recommendations, limit),
    }
    if ranked["requirement_relaxed"]:
        result["requirement_relaxed"] = "too few games had every boost tag, so picks need only one of them"
    if ranked["requirement_unenforced"]:
        result["boost_tags_not_enforced"] = ranked["requirement_unenforced"]
    if not_found:
        result["like_games_not_found"] = not_found
    if unknown_boost or unknown_avoid:
        result["unknown_tags_ignored"] = unknown_boost | unknown_avoid
    if include_backlog:
        enforced = [tag for tag in required if tag not in ranked["requirement_unenforced"]]
        backlog = [
            (g["appid"], g["name"], g["tags"] or {})
            for g in games.values()
            if g["tags"] and is_backlog(g) and fits_tags(g["tags"], enforced, avoid, ranked["requirement_relaxed"])
        ]
        backlog_picks = score_backlog(backlog, taste, df_table)[:limit]
        result["backlog"] = [
            {**pick, "playtime_hours": round(games[rec["appid"]]["playtime_hours"], 1)}
            for rec, pick in zip(backlog_picks, compact(backlog_picks, limit), strict=True)
        ]
    return result


def look_up_game(name: str) -> dict[str, object]:
    """Look up one game on Steam: its tags, whether the player owns it, and how well it fits their taste profile.

    Args:
      name: The game's name; the best Steam store match is used.
    """
    _, games, df_table, profile = ensure_loaded()
    found = find_game(name)
    if found is None:
        return {"error": f"No Steam game with tags found for {name!r}"}
    fit = score(found["appid"], found["name"], found["tags"], profile, norm(profile), df_table)
    owned = games.get(found["appid"])
    strongest = sorted(found["tags"], key=found["tags"].__getitem__, reverse=True)[:10]
    return {
        "name": found["name"],
        "appid": found["appid"],
        "top_tags": strongest,
        "fit_score": round(fit["score"], 3),
        "shared_with_profile": fit["matched_tags"],
        "owned": owned is not None,
        "playtime_hours": round(owned["playtime_hours"], 1) if owned else None,
    }


def update_preferences(
    add_like_games: list[str] | None = None,
    add_boost_tags: list[str] | None = None,
    add_avoid_tags: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, object]:
    """Save taste preferences that apply to every future recommendation, until removed.

    Only call this when the player asks to remember something permanently, not for a one-off request.

    Args:
      add_like_games: Game names to always steer toward.
      add_boost_tags: Steam user tags to always weigh more (a standing taste, not a filter: picks won't be
        required to have them).
      add_avoid_tags: Steam user tags to always steer away from.
      remove: Game names or tags to remove from every saved list.
    """
    _, _, df_table, _ = ensure_loaded()
    boost, unknown_boost = canonical_tags(add_boost_tags, df_table)
    avoid, unknown_avoid = canonical_tags(add_avoid_tags, df_table)

    preferences = load_preferences()
    preferences["like_games"] = without(merge(preferences["like_games"], add_like_games), remove)
    preferences["boost_tags"] = without(merge(preferences["boost_tags"], boost), remove)
    preferences["avoid_tags"] = without(merge(preferences["avoid_tags"], avoid), remove)
    save_preferences(preferences)

    result: dict[str, object] = {"saved_preferences": preferences}
    if unknown_boost or unknown_avoid:
        result["unknown_tags_not_saved"] = unknown_boost | unknown_avoid
    return result


def refresh_library() -> dict[str, object]:
    """Refetch the player's Steam library, playtimes, game tags and HowLongToBeat times, replacing the cache.

    Takes a minute or two (one request per owned game). Use when the player bought or played games since the
    last refresh.
    """
    old_games = state["games"] or library.load_library()[1]
    old_hours = {appid: game["playtime_hours"] for appid, game in old_games.items()}

    state["player_games"], state["games"] = library.refresh_library()
    state["profile"] = None
    reference_cache.clear()
    _, games, _, profile = ensure_loaded()

    new_games = [g["name"] for appid, g in games.items() if appid not in old_hours]
    played_more = {
        g["name"]: round(g["playtime_hours"] - old_hours[appid], 1)
        for appid, g in games.items()
        if appid in old_hours and g["playtime_hours"] - old_hours[appid] >= 0.1
    }
    return {
        "library_size": len(games),
        "new_games": new_games,
        "hours_played_since_last_refresh": played_more,
        "top_tags": rounded(profile, top_tags(profile, 10)),
    }


def update_background_corpus(fetch_new_listings: bool = False) -> dict[str, object]:
    """Grow the Steam-wide tag corpus that decides how distinctive each tag is, then rebuild that table.

    SLOW: always confirm with the player first. Without new listings it only retries failed fetches (minutes);
    with new listings it pulls ~4,000 more games at 1 request/second (can take over an hour).

    Args:
      fetch_new_listings: Pull SteamSpy's popular-game listings for new games, not just retry failed ones.
    """
    df_table = build_steam_collection(fetch_pages=fetch_new_listings)
    state["df_table"] = df_table
    state["profile"] = None
    return {"corpus_games": df_table["n"], "distinct_tags": len(df_table["df"])}


def picks_with_reasons(picks: object, limit: int = 5) -> list[dict[str, object]]:
    """The logged picks' name, score and matched tags: the reason they were picked, so it can be retold faithfully."""
    if not isinstance(picks, list):
        return []
    return [
        {"name": pick["name"], "score": pick["score"], "matched_tags": pick["matched_tags"]}
        for pick in cast(list[dict[str, object]], picks[:limit])
    ]


def get_recommendation_history(session: str | None = None, limit: int = 5) -> dict[str, object]:
    """Recommendation requests from the session logs, newest first: what was asked, how it was steered, top picks.

    Describe past picks only from their matched tags; the log holds nothing else about them.

    Args:
      session: A session id from list_past_sessions, or "all" for every session. Omit for the current session.
      limit: How many requests to return.
    """
    if session == "all":
        sessions = [summary["session"] for summary in reversed(list_sessions())]
    else:
        sessions = [session or state["session"]]

    entries: list[dict[str, object]] = []
    for session_id in sessions:
        for turn in reversed(load_session(session_id)):
            for call in turn["tool_calls"]:
                result = call["result"]
                if call["name"] != "recommend_games" or not isinstance(result, dict):
                    continue
                entries.append(
                    {
                        "session": session_id,
                        "timestamp": turn["timestamp"],
                        "prompt": turn["prompt"],
                        "applied": result.get("applied"),
                        "top_picks": picks_with_reasons(result.get("recommendations")),
                        "backlog_picks": picks_with_reasons(result.get("backlog")),
                    }
                )
            if len(entries) >= limit:
                return {"history": entries[:limit]}
    return {"history": entries}


def list_past_sessions(limit: int = 10) -> dict[str, object]:
    """Logged chat sessions, newest first, with their first prompt, so the player can pick one to compare against.

    Args:
      limit: How many sessions to return.
    """
    sessions = list(reversed(list_sessions()))[:limit]
    return {"current_session": state["session"], "sessions": sessions}


def note_request(
    like_games: list[str] | None = None,
    boost_tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    notes: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, object]:
    """Record what the player wants for their next recommendation, without recommending yet.

    Use while the player is still describing what they want. Everything noted is applied automatically by
    recommend_games for the rest of this session. Tags are checked right away so bad ones can be fixed early.

    Args:
      like_games: Game names they want something like.
      boost_tags: Steam user tags the picks must have ("I want a shooter", "has to be co-op").
      avoid_tags: Steam user tags they want to stay away from.
      notes: Anything else they said that matters for the picks, in a few words each.
      remove: Game names, tags or notes they took back.
    """
    draft = state["draft"]
    result: dict[str, object] = {}
    if boost_tags or avoid_tags:
        _, _, df_table, _ = ensure_loaded()
        boost, unknown_boost = canonical_tags(boost_tags, df_table)
        avoid, unknown_avoid = canonical_tags(avoid_tags, df_table)
        draft["boost_tags"] = merge(draft["boost_tags"], boost)
        draft["avoid_tags"] = merge(draft["avoid_tags"], avoid)
        if unknown_boost or unknown_avoid:
            result["unknown_tags_not_noted"] = unknown_boost | unknown_avoid
    draft["like_games"] = merge(draft["like_games"], like_games)
    draft["notes"] = merge(draft["notes"], notes)
    for key in ("like_games", "boost_tags", "avoid_tags", "notes"):
        draft[key] = without(draft[key], remove)
    result["request_so_far"] = draft
    return result


def start_new_session() -> dict[str, object]:
    """Close this session's log and start a fresh one, with no memory of this conversation.

    Call when the player asks for a new session or a clean slate. It takes effect after this reply, so finish the
    reply first. Saved preferences still apply; use update_preferences to change them.
    """
    state["new_session_requested"] = True
    return {"closed_session": state["session"], "status": "a new session starts with the next prompt"}


TOOLS = [
    get_taste_profile,
    recommend_games,
    look_up_game,
    update_preferences,
    refresh_library,
    update_background_corpus,
    note_request,
    get_recommendation_history,
    list_past_sessions,
    start_new_session,
]
