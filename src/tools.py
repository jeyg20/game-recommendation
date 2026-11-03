"""The functions the assistant can call. Each returns JSON-serializable data for the model.

Signatures and docstrings are turned into Gemini function declarations, so the `Args:` sections are what the model
reads: keep them accurate. Library, df table and profile are loaded once and reused until a refresh tool resets them.
"""

import difflib
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import timedelta
from typing import TypedDict, cast

from src import friends, library, steam_api, steamspy, wishlist, xbox
from src.constraints import candidate_hours, known_hours, with_hours, within_length, within_price
from src.explain import explain
from src.feedback import (
    RATINGS,
    Feedback,
    FeedbackGame,
    Rating,
    empty_feedback,
    find_rated,
    load_feedback,
    names,
    never_recommend,
    rate,
    save_feedback,
    unrate,
)
from src.history import list_sessions, load_session
from src.library import LibraryGame
from src.outcomes import PLAYED_HOURS, pick_outcomes
from src.preferences import Preferences, load_preferences, merge, save_preferences, without
from src.recommend import (
    WILDCARD_PICKS,
    Recommendation,
    enrich_tags,
    fits_tags,
    recommend,
    score,
    score_backlog,
    wildcards,
)
from src.steam_api import OwnedGame
from src.tag_profile import (
    DfTable,
    TagVector,
    adjust_profile,
    build_profile,
    build_steam_collection,
    load_df_table,
    norm,
    recency_factor,
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
    max_hours: float | None  # HLTB main story limit (constraints.py)
    max_price_usd: float | None  # current price limit, after any discount


def empty_draft() -> RequestDraft:
    return {"like_games": [], "boost_tags": [], "avoid_tags": [], "notes": [], "max_hours": None, "max_price_usd": None}


class State(TypedDict):
    player_games: list[OwnedGame] | None
    games: dict[int, LibraryGame] | None
    df_table: DfTable | None
    profile: TagVector | None
    weights: dict[int, float] | None  # appid -> its weight in `profile`, set with it (explain_pick traces by them)
    session: str  # set by the Assistant: the session being logged
    new_session_requested: bool  # set by start_new_session, acted on by the Assistant once the turn ends
    draft: RequestDraft  # reset with every new session
    xbox: xbox.XboxLibrary | None  # the Xbox games, read with the profile; None until refresh_xbox first fetches them


state: State = {
    "player_games": None,
    "games": None,
    "df_table": None,
    "profile": None,
    "weights": None,
    "session": "",
    "new_session_requested": False,
    "draft": empty_draft(),
    "xbox": None,
}
reference_cache: dict[str, ReferenceGame | None] = {}  # lowercased name -> best match


def ensure_loaded() -> tuple[list[OwnedGame], dict[int, LibraryGame], DfTable, TagVector]:
    """Load the cached library, df table and library profile on first use."""
    if state["player_games"] is None or state["games"] is None:
        state["player_games"], state["games"] = library.load_library()
    if state["df_table"] is None:
        state["df_table"] = load_df_table()
    if state["profile"] is None:
        # only once titles were fetched (refresh_xbox): reading the cache may refetch it daily, but never sets it up
        if state["xbox"] is None and xbox.is_set_up():
            state["xbox"] = xbox.load_xbox({game["appid"] for game in state["player_games"]})
        state["weights"] = library_weights(
            state["games"].values(),
            last_played_by_appid(state["player_games"]),
            recent_appids=played_recently(state["player_games"]),
            achievement_share=library.load_achievement_shares(state["player_games"]),
        )
        state["profile"] = weighted_profile(
            state["games"].values(), state["weights"], state["df_table"], xbox_entries(state["xbox"])
        )
        if not state["profile"]:
            raise RuntimeError("Empty taste profile: no owned game has both playtime and tags")
    return state["player_games"], state["games"], state["df_table"], state["profile"]


def last_played_by_appid(player_games: Iterable[OwnedGame]) -> dict[int, int]:
    """appid -> Steam's rtime_last_played (unix seconds, 0 = never). LibraryGame doesn't carry it."""
    return {game["appid"]: game["rtime_last_played"] for game in player_games}


def played_recently(player_games: Iterable[OwnedGame]) -> set[int]:
    """Appids with playtime in the last two weeks (Steam's playtime_2weeks, absent otherwise)."""
    return {game["appid"] for game in player_games if game.get("playtime_2weeks", 0) > 0}


def library_weights(
    games: Iterable[LibraryGame],
    last_played: Mapping[int, int] | None = None,
    now: float | None = None,
    recent_appids: Collection[int] = (),
    achievement_share: Mapping[int, float] | None = None,
) -> dict[int, float]:
    """appid -> the weight each tagged owned game gets in the library profile (games without tags add nothing).

    Abandoned games (see is_abandoned) weigh -ABANDON_WEIGHT; without `last_played` none count as abandoned.
    Other games' weights fade with time since last played (recency_factor), except games in `recent_appids`
    (played in the last two weeks). `achievement_share` (appid -> unlocked share) feeds taste_weight's completion.
    The abandoned penalty doesn't fade: an old abandonment is no weaker evidence against its tags.
    """
    last_played = last_played or {}
    achievement_share = achievement_share or {}
    now = time.time() if now is None else now

    def weight(game: LibraryGame) -> float:
        played = last_played.get(game["appid"], 0)
        if is_abandoned(game, played, now):
            return -ABANDON_WEIGHT
        base = taste_weight(
            game["playtime_hours"],
            game["main_story_hours"],
            game["tags"] or {},
            achievement_share.get(game["appid"]),
        )
        return base if game["appid"] in recent_appids else base * recency_factor(played, now)

    return {g["appid"]: weight(g) for g in games if g["tags"]}


def weighted_profile(
    games: Iterable[LibraryGame],
    weights: Mapping[int, float],
    df_table: DfTable,
    extra: Sequence[tuple[float, dict[str, int]]] = (),
) -> TagVector:
    """sum(weight * tag_vector) over the games `weights` has a weight for, plus the `extra` (weight, tags) entries
    (games owned outside Steam, e.g. on Xbox)."""
    owned = [(weights[g["appid"]], g["tags"] or {}) for g in games if g["appid"] in weights]
    return build_profile([*owned, *extra], df_table)


def xbox_entries(library: xbox.XboxLibrary | None) -> list[tuple[float, dict[str, int]]]:
    """The Xbox-only games as extra profile entries; none when Xbox isn't set up, so the profile is Steam's alone."""
    return [(game["weight"], game["tags"]) for game in library["games"]] if library else []


def xbox_appids() -> set[int]:
    """Every game matched on the player's Xbox: owned there, so never recommended."""
    return set(state["xbox"]["appids"]) if state["xbox"] else set()


def library_profile(
    games: Iterable[LibraryGame],
    df_table: DfTable,
    last_played: Mapping[int, int] | None = None,
    now: float | None = None,
    recent_appids: Collection[int] = (),
    achievement_share: Mapping[int, float] | None = None,
) -> TagVector:
    """The taste profile of these owned games, weighted by library_weights. Shared with the offline evaluation,
    which leaves one game out.
    """
    games = list(games)
    return weighted_profile(games, library_weights(games, last_played, now, recent_appids, achievement_share), df_table)


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


ABANDON_MIN_DAYS = 120  # barely started and untouched for this long: the player tried it and left
ABANDON_WEIGHT = 0.3  # an abandoned game's weight against its tags, against up to 1.0 for a game played through
SECONDS_PER_DAY = 86_400


def is_abandoned(game: LibraryGame, last_played: int, now: float) -> bool:
    """Launched, barely started (is_backlog) and not played for over ABANDON_MIN_DAYS. A signal against its tags.

    `last_played` is Steam's rtime_last_played in unix seconds; 0 (no date) is no evidence, so never abandoned.
    Never-launched games say nothing either way.
    """
    if game["playtime_hours"] == 0 or not last_played or not is_backlog(game):
        return False
    return (now - last_played) / SECONDS_PER_DAY > ABANDON_MIN_DAYS


def backlog_games(games: dict[int, LibraryGame], player_games: Iterable[OwnedGame]) -> list[LibraryGame]:
    """Owned games still worth suggesting: never launched or barely started, minus the ones already abandoned."""
    last_played = last_played_by_appid(player_games)
    now = time.time()
    return [g for g in games.values() if is_backlog(g) and not is_abandoned(g, last_played.get(g["appid"], 0), now)]


def compact(
    recommendations: Sequence[Recommendation], limit: int, wishlisted: set[int] | None = None
) -> list[dict[str, object]]:
    """The picks as the model sees them. `wishlisted` adds `on_wishlist` (left out for backlog games: they're owned)."""
    return [
        {
            "name": rec["name"],
            "appid": rec["appid"],
            "score": round(rec["score"], 3),
            "positive_ratio": round(rec["positive_ratio"], 2) if rec["positive_ratio"] is not None else None,
            "matched_tags": rec["matched_tags"],
            "price_usd": rec["price_usd"],
            "discount_percent": rec["discount_percent"],
            **({"on_wishlist": rec["appid"] in wishlisted} if wishlisted is not None else {}),
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
    player_games, games, _, profile = ensure_loaded()
    played = sorted(games.values(), key=lambda g: g["playtime_hours"], reverse=True)
    return {
        "top_tags": rounded(profile, top_tags(profile, top_n)),
        "most_played": [{"name": g["name"], "hours": round(g["playtime_hours"], 1)} for g in played[:10]],
        "recently_played": [
            {"name": game["name"], "hours_last_2_weeks": round(game.get("playtime_2weeks", 0) / 60, 1)}
            for game in sorted(player_games, key=lambda game: game.get("playtime_2weeks", 0), reverse=True)
            if game.get("playtime_2weeks", 0) > 0 and game["appid"] in games  # games only, as most_played
        ],
        "backlog_games": len(backlog_games(games, player_games)),
        "library_size": len(games),
        "xbox_games": len(state["xbox"]["games"]) if state["xbox"] else 0,  # Xbox-only games counted in the profile
        "saved_preferences": load_preferences(),
    }


class Steering(TypedDict):
    """A request's steering of the library profile, and what went into it (for the tools to report)."""

    taste: TagVector  # the steered profile; the library profile itself when nothing steers it
    required: list[str]  # this request's and the draft's boost tags: must-haves
    soft_boost: list[str]  # saved boost tags: standing tastes, weight only
    avoid: list[str]
    references: list[ReferenceGame]  # the like_games found on Steam
    not_found: list[str]  # like_games with no Steam match
    unknown_tags: dict[str, list[str]]  # boost/avoid tags that aren't Steam tags -> suggestions
    liked: list[FeedbackGame]  # rated games steering toward their tags
    disliked: list[FeedbackGame]  # rated games steering away from theirs
    feedback: Feedback  # every rating, applied or not: disliked and dismissed games never come back either way


def steer(
    profile: TagVector,
    df_table: DfTable,
    like_games: list[str] | None = None,
    boost_tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    use_saved_preferences: bool = True,
) -> Steering:
    """The profile as recommend_games ranks against it: steered by saved preferences and ratings (unless
    use_saved_preferences is off), this session's draft and this request's arguments. explain_pick shares it, so a
    "why" is answered against the same profile the pick came from.
    """
    saved: Preferences = (
        load_preferences() if use_saved_preferences else {"like_games": [], "boost_tags": [], "avoid_tags": []}
    )
    draft = state["draft"]

    # this request's boosts are must-haves; saved ones are standing tastes, so they only add weight
    required, unknown_required = canonical_tags(merge(draft["boost_tags"], boost_tags), df_table)
    soft_boost, unknown_soft = canonical_tags(saved["boost_tags"], df_table)
    boost = merge(soft_boost, required)
    avoid, unknown_avoid = canonical_tags(merge(merge(saved["avoid_tags"], draft["avoid_tags"]), avoid_tags), df_table)
    references: list[ReferenceGame] = []
    not_found: list[str] = []
    for name in merge(merge(saved["like_games"], draft["like_games"]), like_games):
        found = find_game(name)
        if found is None:
            not_found.append(name)
        else:
            references.append(found)
    # rated games: disliked and dismissed ones never come back; liked and disliked ones steer like saved preferences
    feedback = load_feedback()
    rated = feedback if use_saved_preferences else empty_feedback()
    liked = list(rated["liked"].values())
    disliked = list(rated["disliked"].values())
    negatives = [tag_vector(game["tags"], df_table) for game in disliked]

    steered = bool(references or liked or negatives or boost or avoid)
    taste = (
        adjust_profile(
            profile, [tag_vector(game["tags"], df_table) for game in [*references, *liked]], boost, avoid, negatives
        )
        if steered
        else profile
    )
    return {
        "taste": taste,
        "required": required,
        "soft_boost": soft_boost,
        "avoid": avoid,
        "references": references,
        "not_found": not_found,
        "unknown_tags": unknown_soft | unknown_required | unknown_avoid,
        "liked": liked,
        "disliked": disliked,
        "feedback": feedback,
    }


def recommend_games(
    like_games: list[str] | None = None,
    boost_tags: list[str] | None = None,
    avoid_tags: list[str] | None = None,
    limit: int = 10,
    include_backlog: bool = True,
    use_saved_preferences: bool = True,
    on_sale_only: bool = False,
    include_wildcards: bool = True,
    max_hours: float | None = None,
    max_price_usd: float | None = None,
) -> dict[str, object]:
    """Rank games the player doesn't own against their taste profile, optionally steered for this request.

    Everything recorded with note_request this session is applied automatically, so only pass what is new.

    Candidates come from SteamSpy (popular, well-reviewed games). Each result has a cosine score in [0, 1] and the
    tags it shares with the profile, which is the reason it was picked, plus its price: SteamSpy's US price in dollars
    (after any discount) and discount_percent, up to a day old. on_wishlist says the player already wishlisted it.
    New tag pools and game tags are fetched at ~1 request/second, so a first run with new tags can take a minute or two.
    Games the player has on Xbox (once refresh_xbox has run) are never recommended, as picks, wildcards or backlog.

    Afterwards: show the picks as Markdown tables (recommendations, wildcards, backlog). If the player asks why a pick
    was chosen, call explain_pick for that game and answer from it. If they react to a game ("not for me", "loved
    it"), call rate_games. stale_pools can't be refreshed by any tool.

    Args:
      like_games: Game names to steer toward ("more like Hollow Knight and Celeste"). Each is looked up on Steam.
      boost_tags: Steam user tags the picks must have, e.g. ["Shooter"] or ["Roguelike", "Co-op"] (all of them when
        enough games qualify), and that weigh more in the ranking. Must be real Steam tag names.
      avoid_tags: Steam user tags to steer away from; games with one among their strongest tags are dropped.
      limit: How many recommendations (and backlog games) to return.
      include_backlog: Also rank owned games the player never launched or only tried briefly.
      use_saved_preferences: Apply the player's saved preferences on top of this request's.
      on_sale_only: Keep only discounted picks ("which of my picks are on sale?"); may return fewer than limit.
        Applies to wildcards too.
      include_wildcards: Also return a few wildcards: well-fitting games from tags just outside the player's usual
        ones, each with the via_tag it came from. Left out when boost_tags are required.
      max_hours: Longest HowLongToBeat main story allowed ("under 10 hours", "something short"). Picks with no HLTB
        time are kept after the ones known to fit, flagged length_unknown. Applies to backlog and wildcards too.
      max_price_usd: Most the player would pay now, in US dollars after any discount ("nothing over $20", "cheap").
        Picks with no known price are dropped. Applies to wildcards too; backlog games are owned.
    """
    player_games, games, df_table, profile = ensure_loaded()
    steering = steer(profile, df_table, like_games, boost_tags, avoid_tags, use_saved_preferences)
    taste, required, soft_boost = steering["taste"], steering["required"], steering["soft_boost"]
    avoid, references, liked, feedback = (
        steering["avoid"],
        steering["references"],
        steering["liked"],
        steering["feedback"],
    )

    draft = state["draft"]
    max_hours = max_hours if max_hours is not None else draft["max_hours"]
    max_price_usd = max_price_usd if max_price_usd is not None else draft["max_price_usd"]

    on_xbox = xbox_appids()  # a Steam backlog game the player also has on Xbox may be played there: not backlog
    excluded = (
        {game["appid"] for game in player_games}
        | {game["appid"] for game in [*references, *liked]}
        | never_recommend(feedback)
        | on_xbox
    )
    ranked = recommend(taste, top_tags(taste, 8), excluded, df_table, avoid, required_tags=required)
    recommendations = ranked["recommendations"]
    if on_sale_only:
        recommendations = [rec for rec in recommendations if (rec["discount_percent"] or 0) > 0]
    recommendations, hours = within_length(
        within_price(recommendations, max_price_usd), limit, max_hours, candidate_hours
    )

    wishlisted = wishlist.wishlisted_appids()
    result: dict[str, object] = {
        "applied": {
            "like_games": [ref["name"] for ref in references],
            "boost_tags": required,
            "saved_boost_tags": soft_boost,
            "avoid_tags": avoid,
            "from_saved_preferences": use_saved_preferences,
            "player_notes": draft["notes"],
            # only when set, so output without limits is unchanged
            **({"max_hours": max_hours} if max_hours is not None else {}),
            **({"max_price_usd": max_price_usd} if max_price_usd is not None else {}),
            # only once the player has rated something, so output without feedback is unchanged
            **(
                {
                    "feedback_applied": {
                        "liked": len(liked),
                        "disliked": len(steering["disliked"]),
                        "not_interested": len(feedback["not_interested"]),
                    }
                }
                if any(feedback.values())
                else {}
            ),
        },
        "steered_top_tags": rounded(taste, top_tags(taste, 10)),
        "candidate_pools": ranked["pool_tags"],
        "recommendations": with_hours(compact(recommendations, limit, wishlisted), hours),
    }
    if ranked["requirement_relaxed"]:
        result["requirement_relaxed"] = "too few games had every boost tag, so picks need only one of them"
    if ranked["requirement_unenforced"]:
        result["boost_tags_not_enforced"] = ranked["requirement_unenforced"]
    if ranked["stale_pools"]:
        result["stale_pools"] = ranked["stale_pools"]
    if steering["not_found"]:
        result["like_games_not_found"] = steering["not_found"]
    if steering["unknown_tags"]:
        result["unknown_tags_ignored"] = steering["unknown_tags"]
    if include_backlog:
        enforced = [tag for tag in required if tag not in ranked["requirement_unenforced"]]
        backlog = [
            (g["appid"], g["name"], g["tags"] or {})
            for g in backlog_games(games, player_games)
            if g["tags"]
            and g["appid"] not in on_xbox
            and fits_tags(g["tags"], enforced, avoid, ranked["requirement_relaxed"])
        ]
        backlog_picks, owned_hours = within_length(
            score_backlog(backlog, taste, df_table),
            limit,
            max_hours,
            lambda picks: {p["appid"]: known_hours(games[p["appid"]]["main_story_hours"]) for p in picks},
        )
        backlog_picks = backlog_picks[:limit]
        result["backlog"] = with_hours(
            [
                {**pick, "playtime_hours": round(games[rec["appid"]]["playtime_hours"], 1)}
                for rec, pick in zip(backlog_picks, compact(backlog_picks, limit), strict=True)
            ],
            owned_hours,
        )
    # a wildcard is outside the player's usual tags by definition, so it can't also carry a required one
    if include_wildcards and not required:
        main_picks = {rec["appid"] for rec in ranked["recommendations"]}
        wild = wildcards(taste, ranked["pool_tags"], excluded | main_picks, df_table, avoid)
        if on_sale_only:
            wild = [rec for rec in wild if (rec["discount_percent"] or 0) > 0]
        wild, wild_hours = within_length(within_price(wild, max_price_usd), WILDCARD_PICKS, max_hours, candidate_hours)
        wild = wild[:WILDCARD_PICKS]
        result["wildcards"] = with_hours(
            [
                {**pick, "via_tag": rec["via_tag"]}
                for rec, pick in zip(wild, compact(wild, WILDCARD_PICKS, wishlisted), strict=True)
            ],
            wild_hours,
        )
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

    SLOW: always confirm with the player first. Without new listings it only retries failed and untagged fetches
    (minutes); with new listings it pulls ~4,000 more games at 1 request/second (can take over an hour).

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
    max_hours: float | None = None,
    max_price_usd: float | None = None,
) -> dict[str, object]:
    """Record what the player wants for their next recommendation, without recommending yet.

    Use while the player is still describing what they want. Everything noted is applied automatically by
    recommend_games for the rest of this session. Tags are checked right away so bad ones can be fixed early.

    Args:
      like_games: Game names they want something like.
      boost_tags: Steam user tags the picks must have ("I want a shooter", "has to be co-op").
      avoid_tags: Steam user tags they want to stay away from.
      notes: Anything else they said that matters for the picks, in a few words each.
      remove: Game names, tags or notes they took back; "max_hours" or "max_price_usd" drops that limit.
      max_hours: Longest HowLongToBeat main story they'll accept ("under 10 hours", "something short").
      max_price_usd: Most they'd pay now, in US dollars after any discount ("nothing over $20", "cheap").
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
    if max_hours is not None:
        draft["max_hours"] = max_hours
    if max_price_usd is not None:
        draft["max_price_usd"] = max_price_usd
    for key in ("max_hours", "max_price_usd"):
        if key in (remove or []):
            draft[key] = None
    result["request_so_far"] = draft
    return result


def start_new_session() -> dict[str, object]:
    """Close this session's log and start a fresh one, with no memory of this conversation.

    Call when the player asks for a new session or a clean slate. It takes effect after this reply, so finish the
    reply first. Saved preferences still apply; use update_preferences to change them.
    """
    state["new_session_requested"] = True
    return {"closed_session": state["session"], "status": "a new session starts with the next prompt"}


def explain_pick(name: str, steered: bool = False) -> dict[str, object]:
    """Why a game fits the player's taste profile: which tags drove its score, how much each added, and which of the
    player's own games put those tags in the profile. This is the fit to their profile, not a quality judgement.

    Call this for every why-question about a pick ("why Hades?", "why that one?"), owned or not, and answer from its
    tag shares and because_you_played instead of reasoning from the fit scores.

    Args:
      name: The game's name; the best Steam store match is used. Works for owned games too.
      steered: Explain against the profile as steered by this session's noted request and saved preferences (the
        steering recommend_games applies), instead of the plain library profile. Use when the pick came from a
        steered recommendation.
    """
    _, games, df_table, profile = ensure_loaded()
    found = find_game(name)
    if found is None:
        return {"error": f"No Steam game with tags found for {name!r}"}
    result: dict[str, object] = {}
    if steered:
        steering = steer(profile, df_table)
        profile = steering["taste"]
        result["steered_by"] = {
            "like_games": [ref["name"] for ref in steering["references"]],
            "boost_tags": merge(steering["soft_boost"], steering["required"]),
            "avoid_tags": steering["avoid"],
            # only once the player has rated something, as in recommend_games
            **(
                {
                    "liked_games": [game["name"] for game in steering["liked"]],
                    "disliked_games": [game["name"] for game in steering["disliked"]],
                }
                if steering["liked"] or steering["disliked"]
                else {}
            ),
        }
    why = explain(found["tags"], profile, df_table, games.values(), state["weights"] or {})
    owned = games.get(found["appid"])
    return {
        "name": found["name"],
        "appid": found["appid"],
        "owned": owned is not None,
        "fit_score": round(why["score"], 3),
        **result,
        "tags": [
            {
                "tag": part["tag"],
                "contribution": round(part["contribution"], 4),
                "share_of_score": round(part["share_of_score"], 3),
            }
            for part in why["tags"]
        ],
        "other_tags_contribution": round(why["other_tags"], 4),
        "because_you_played": why["because_you_played"],
    }


def get_pick_outcomes(limit: int = 20) -> dict[str, object]:
    """How past recommendations turned out: of every game ever recommended, which the player bought and how much
    they played it, plus how many backlog picks they started. Each game counts once, however often it was picked.

    Use for "how good have your recommendations been?" or "did I play what you recommended?".

    Args:
      limit: How many bought games to list, most-played first.
    """
    _, games, _, _ = ensure_loaded()
    outcomes = pick_outcomes(games)
    picks = outcomes["picks"]
    bought = sorted((pick for pick in picks if pick["bought"]), key=lambda pick: pick["hours"], reverse=True)
    played = [pick for pick in bought if pick["hours"] > PLAYED_HOURS]
    return {
        "summary": {
            "recommended": len(picks),
            "bought": len(bought),
            f"played_over_{PLAYED_HOURS:g}h": len(played),
            "buy_rate": round(len(bought) / len(picks), 3) if picks else None,
            "hit_rate": round(len(played) / len(picks), 3) if picks else None,
            "backlog_recommended": len(outcomes["backlog"]),
            "backlog_started": sum(game["started"] for game in outcomes["backlog"]),
        },
        "bought_games": [
            {
                "name": pick["name"],
                "appid": pick["appid"],
                "hours": round(pick["hours"], 1),
                "first_recommended": pick["first_recommended"],
                "rank": pick["rank"],
                "score": pick["score"],
            }
            for pick in bought[:limit]
        ],
        "note": f"hit_rate counts picks bought and played over {PLAYED_HOURS:g} h. Ownership and playtime are as of "
        "the last library refresh (refresh_library), so recent purchases may be missing.",
    }


def rate_games(
    liked: list[str] | None = None,
    disliked: list[str] | None = None,
    not_interested: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, object]:
    """Save the player's reactions to games, kept across sessions and applied to every recommend_games call.

    Use when the player reacts to picks ("loved Hades", "Dead Cells isn't for me", "not interested in Rust"), rather
    than avoid tags or notes: a rating steers by that one game's tags and hides it for good.
    Liked games pull the ranking toward their tags, disliked ones push it away and never come back as picks,
    not-interested ones are only hidden from future picks. Rating a game again moves it to the new list.

    Args:
      liked: Game names the player liked or wants more of.
      disliked: Game names the player played or tried and didn't like.
      not_interested: Game names the player just doesn't want recommended, without judging their taste.
      remove: Game names to take out of every list.
    """
    feedback = load_feedback()
    not_found: list[str] = []
    for name in remove or []:
        appid = find_rated(feedback, name)
        if appid is None and (found := find_game(name)) is not None:
            appid = found["appid"]
        if appid is None:
            not_found.append(name)
        else:
            unrate(feedback, appid)
    asked: dict[Rating, list[str] | None] = {"liked": liked, "disliked": disliked, "not_interested": not_interested}
    for rating in RATINGS:
        for name in asked[rating] or []:
            found = find_game(name)
            if found is None:
                not_found.append(name)
            else:
                rate(feedback, rating, found["appid"], found["name"], found["tags"])
    save_feedback(feedback)

    result: dict[str, object] = {"feedback": names(feedback)}
    if not_found:
        result["not_found"] = not_found
    return result


def get_feedback() -> dict[str, object]:
    """The games the player rated with rate_games: liked, disliked and not interested."""
    return {"feedback": names(load_feedback())}


def rank_wishlist(limit: int = 10) -> dict[str, object]:
    """Rank the games on the player's Steam wishlist by fit to their taste profile, best first.

    Use for "which game on my wishlist should I get first?". Scores use the plain library profile, with no steering.
    The wishlist is cached for a day; tags of newly wishlisted games are fetched at ~1 request/second. Games SteamSpy
    has no tags for can't be scored and are listed under not_ranked.
    priority is the player's own order on the wishlist page (0 for all until they reorder it).

    Args:
      limit: How many wishlist games to return.
    """
    _, _, df_table, profile = ensure_loaded()
    items = wishlist.load_wishlist()
    if items is None:
        return {"error": "Couldn't read the Steam wishlist: the request to Steam failed. Try again later."}
    if not items:
        return {
            "error": "The Steam wishlist is empty or private. To rank it, set Game details to Public in the Steam "
            "profile's privacy settings."
        }
    appids = [item["appid"] for item in items]
    records = enrich_tags(appids)
    titles = wishlist.names(appids)
    priority = {item["appid"]: item["priority"] for item in items}
    profile_norm = norm(profile)
    ranked = sorted(
        (
            score(appid, titles.get(appid, f"app {appid}"), records[appid]["tags"], profile, profile_norm, df_table)
            for appid in appids
            if appid in records
        ),
        key=lambda rec: rec["score"],
        reverse=True,
    )
    result: dict[str, object] = {
        "wishlist_size": len(items),
        "ranked": [
            {
                "name": rec["name"],
                "appid": rec["appid"],
                "score": round(rec["score"], 3),
                "matched_tags": rec["matched_tags"],
                "priority": priority[rec["appid"]],
            }
            for rec in ranked[:limit]
        ],
    }
    untagged = [titles.get(appid, f"app {appid}") for appid in appids if appid not in records]
    if untagged:
        result["not_ranked"] = {
            "games": untagged,
            "reason": "SteamSpy has no tags for them yet (common for new or unreleased games)",
        }
    return result


FRIENDS_MAX_CANDIDATES = 60  # most-owned friend games tagged and scored; tag fetches for misses run at 1 req/s


def friends_games(
    min_friends: int = 1, limit: int = 10, boost_tags: list[str] | None = None, avoid_tags: list[str] | None = None
) -> dict[str, object]:
    """Rank games the player's Steam friends own (and the player doesn't) by fit to their taste profile, best first.

    Use for "what could I play with my friends?" or co-op picks. Each pick lists the friends who own it. Slow on the
    first call of the day: one Steam request per friend at ~1 request/second, then tags for new games; cached for a
    day after that. Friends with private libraries are skipped and counted. Noted steers aren't applied: pass them.

    Args:
      min_friends: Only games at least this many friends own (2+ for a group game).
      limit: How many games to return.
      boost_tags: Steam user tags every pick must have among its strongest, e.g. ["Co-op"]. They weigh more too.
      avoid_tags: Steam user tags to steer away from; games with one among their strongest tags are dropped.
    """
    player_games, _, df_table, profile = ensure_loaded()
    cache = friends.load_friends()
    if cache is None:
        return {"error": "Couldn't read the Steam friends list: the request failed or the friends list is private."}
    boost, unknown_boost = canonical_tags(boost_tags, df_table)
    avoid, unknown_avoid = canonical_tags(avoid_tags, df_table)
    taste = adjust_profile(profile, [], boost, avoid) if boost or avoid else profile
    excluded = {game["appid"] for game in player_games} | never_recommend(load_feedback()) | xbox_appids()
    owned_by = {
        appid: names
        for appid, names in friends.owners(cache).items()
        if appid not in excluded and len(names) >= max(min_friends, 1)
    }
    candidates = sorted(owned_by, key=lambda appid: len(owned_by[appid]), reverse=True)[:FRIENDS_MAX_CANDIDATES]
    records = enrich_tags(candidates)
    taste_norm = norm(taste)
    titles = cache["game_names"]
    ranked = sorted(
        (
            score(appid, titles.get(str(appid), f"app {appid}"), records[appid]["tags"], taste, taste_norm, df_table)
            for appid in candidates
            if appid in records and fits_tags(records[appid]["tags"], boost, avoid, relaxed=False)
        ),
        key=lambda rec: rec["score"],
        reverse=True,
    )
    private = sum(1 for friend in cache["friends"].values() if friend["appids"] is None)
    result: dict[str, object] = {
        "friends": len(cache["friends"]),
        "private_libraries": private,
        "games_owned_by_friends": len(owned_by),
        "candidates_scored": len(candidates),
        "picks": [
            {
                "name": rec["name"],
                "appid": rec["appid"],
                "score": round(rec["score"], 3),
                "matched_tags": rec["matched_tags"],
                "owned_by": owned_by[rec["appid"]],
            }
            for rec in ranked[:limit]
        ],
    }
    if unknown_boost or unknown_avoid:
        result["unknown_tags_ignored"] = unknown_boost | unknown_avoid
    return result


XBOX_UNMATCHED_SHOWN = 20


def refresh_xbox() -> dict[str, object]:
    """Read the player's Xbox games (console, Game Pass, Xbox PC app) from OpenXBL now, match new ones to Steam games
    and rebuild the taste profile with them. Matched games count in the profile by achievement progress (Xbox gives
    no hours) unless the Steam library has them, and are never recommended.

    Call when the player says they play on Xbox or Game Pass, or after they bought something on Xbox. The first call
    is slow (a few minutes: one Steam store lookup per Xbox game at 1 per second); later calls only look up new games.
    Needs an OpenXBL key (XBOX_API_KEY in .env).
    """
    player_games = state["player_games"] or library.load_library()[0]
    loaded = xbox.load_xbox({game["appid"] for game in player_games}, max_age=timedelta(0))
    if loaded is None:
        return {
            "error": "Couldn't read the Xbox games: no XBOX_API_KEY in .env, or the OpenXBL request failed and "
            "nothing is cached yet. Try again later."
        }
    state["xbox"] = loaded
    state["profile"] = None
    _, _, _, profile = ensure_loaded()
    result: dict[str, object] = {
        "titles": loaded["titles"],
        "matched": loaded["matched"],
        "tagged": len(loaded["games"]),
        "new_matches": loaded["new_matches"],
        "unmatched": loaded["unmatched"][:XBOX_UNMATCHED_SHOWN],
        "unmatched_count": len(loaded["unmatched"]),
        "skipped_steam_duplicates": loaded["steam_duplicates"],
        "top_tags": rounded(profile, top_tags(profile, 10)),
    }
    if loaded["fetch_failed"]:
        result["openxbl_failed"] = f"OpenXBL didn't answer: used the Xbox games cached at {loaded['fetched_at']}"
    return result


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
    explain_pick,
    get_pick_outcomes,
    rate_games,
    get_feedback,
    rank_wishlist,
    friends_games,
    refresh_xbox,
]
