import pytest

from src import mcp_server, tools
from src.library import LibraryGame
from src.tag_profile import DfTable


def library_game(playtime_hours: float, main_story_hours: float | None = None) -> LibraryGame:
    return {
        "appid": 1,
        "name": "Game",
        "playtime_hours": playtime_hours,
        "main_story_hours": main_story_hours,
        "review_score": None,
        "genres": None,
        "tags": None,
    }


def test_is_backlog_never_launched() -> None:
    assert tools.is_backlog(library_game(0, 30))


def test_is_backlog_tried_briefly() -> None:
    assert tools.is_backlog(library_game(1.0))  # no HLTB time
    assert tools.is_backlog(library_game(1.0, 10))  # 10% of the story


def test_is_backlog_not_for_finished_short_games_or_long_plays() -> None:
    assert not tools.is_backlog(library_game(1.0, 1.5))  # most of a short story
    assert not tools.is_backlog(library_game(2.0))


NOW = 1_800_000_000.0
DAY = 86_400


def days_ago(days: float) -> int:
    return int(NOW - days * DAY)


def test_is_abandoned_needs_a_launch_a_barely_started_game_and_a_long_absence() -> None:
    assert not tools.is_abandoned(library_game(0), days_ago(1000), NOW)  # never launched: no signal
    assert tools.is_abandoned(library_game(1.9), days_ago(121), NOW)
    assert not tools.is_abandoned(library_game(1.9), days_ago(119), NOW)  # tried recently, may come back
    assert not tools.is_abandoned(library_game(2.0), days_ago(1000), NOW)  # past barely started
    assert not tools.is_abandoned(library_game(1.0, 1.5), days_ago(1000), NOW)  # a short game, finished
    assert not tools.is_abandoned(library_game(1.0), 0, NOW)  # no last-played date


def tagged(appid: int, playtime_hours: float, tags: dict[str, int]) -> LibraryGame:
    return {**library_game(playtime_hours), "appid": appid, "tags": tags}


def test_abandoned_game_weakens_its_tags_in_the_library_profile(df_table: DfTable) -> None:
    loved = tagged(1, 50.0, {"RPG": 1, "Horror": 1})
    dropped = tagged(2, 0.5, {"Horror": 1})
    last_played = {1: days_ago(10), 2: days_ago(500)}
    without = tools.library_profile([loved], df_table, last_played, NOW)
    with_dropped = tools.library_profile([loved, dropped], df_table, last_played, NOW)
    assert with_dropped["Horror"] < without["Horror"]
    assert with_dropped["RPG"] == without["RPG"]


def test_game_played_last_week_outweighs_the_same_playtime_five_years_ago(df_table: DfTable) -> None:
    game = tagged(1, 50.0, {"RPG": 1})
    last_week = tools.library_profile([game], df_table, {1: days_ago(7)}, NOW)
    five_years_ago = tools.library_profile([game], df_table, {1: days_ago(5 * 365)}, NOW)
    assert last_week["RPG"] > 1.9 * five_years_ago["RPG"]  # 0.99 against 0.50


def test_playtime_in_the_last_two_weeks_overrides_an_old_last_played_date(df_table: DfTable) -> None:
    game = tagged(1, 50.0, {"RPG": 1})
    stale = {1: days_ago(1000)}
    assert tools.library_profile([game], df_table, stale, NOW, recent_appids={1}) == tools.library_profile(
        [game], df_table, {1: int(NOW)}, NOW
    )


def test_abandoned_penalty_does_not_fade_with_time(df_table: DfTable) -> None:
    loved = tagged(1, 50.0, {"RPG": 1, "Horror": 1})
    dropped = tagged(2, 0.5, {"Horror": 1})
    profiles = [
        tools.library_profile([loved, dropped], df_table, {1: int(NOW), 2: days_ago(days)}, NOW) for days in (200, 2000)
    ]
    assert profiles[0]["Horror"] == profiles[1]["Horror"]


def test_recency_floor_of_one_leaves_the_profile_as_before(df_table: DfTable, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.tag_profile.RECENCY_FLOOR", 1.0)
    games = [tagged(1, 50.0, {"RPG": 1}), tagged(2, 10.0, {"Horror": 1, "RPG": 1})]
    weighted = [(tools.taste_weight(g["playtime_hours"], None, g["tags"] or {}), g["tags"] or {}) for g in games]
    last_played = {1: days_ago(3000), 2: days_ago(40)}
    assert tools.library_profile(games, df_table, last_played, NOW) == tools.build_profile(weighted, df_table)


def test_taste_profile_lists_recently_played_games(monkeypatch: pytest.MonkeyPatch) -> None:
    owned = [
        {"appid": 1, "name": "Old", "rtime_last_played": days_ago(400)},
        {"appid": 2, "name": "Now", "rtime_last_played": days_ago(1), "playtime_2weeks": 90},
        {"appid": 3, "name": "Not a game", "rtime_last_played": days_ago(1), "playtime_2weeks": 600},
    ]
    games = {appid: tagged(appid, 10.0, {"RPG": 1}) for appid in (1, 2)}
    monkeypatch.setattr(tools, "ensure_loaded", lambda: (owned, games, {}, {"RPG": 1.0}))
    monkeypatch.setattr(tools, "load_preferences", dict)
    assert tools.get_taste_profile(5)["recently_played"] == [{"name": "Now", "hours_last_2_weeks": 1.5}]


def test_never_launched_game_leaves_the_library_profile_unchanged(df_table: DfTable) -> None:
    loved = tagged(1, 50.0, {"RPG": 1, "Horror": 1})
    unplayed = tagged(2, 0.0, {"Horror": 1, "Puzzle": 1})
    last_played = {1: days_ago(10), 2: 0}
    assert tools.library_profile([loved, unplayed], df_table, last_played, NOW) == tools.library_profile(
        [loved], df_table, last_played, NOW
    )


def test_backlog_games_leaves_out_abandoned_ones() -> None:
    games = {1: tagged(1, 0.0, {"RPG": 1}), 2: tagged(2, 0.5, {"RPG": 1}), 3: tagged(3, 0.5, {"RPG": 1})}
    now = tools.time.time()
    player_games = [
        {"appid": 1, "rtime_last_played": 0},
        {"appid": 2, "rtime_last_played": int(now - 500 * DAY)},
        {"appid": 3, "rtime_last_played": int(now - 5 * DAY)},
    ]
    backlog = tools.backlog_games(games, player_games)  # pyright: ignore[reportArgumentType]
    assert [g["appid"] for g in backlog] == [1, 3]


def test_canonical_tags_fixes_case_and_suggests_for_unknown(df_table: DfTable) -> None:
    known, unknown = tools.canonical_tags([" roguelike ", "SHOOTER", "Shoter", "Zzzz"], df_table)
    assert known == ["Roguelike", "Shooter"]
    assert unknown == {"Shoter": ["Shooter"], "Zzzz": []}


def test_canonical_tags_of_nothing(df_table: DfTable) -> None:
    assert tools.canonical_tags(None, df_table) == ([], {})


def loaded(df_table: DfTable) -> None:
    """Fill the shared state so ensure_loaded doesn't read the library."""
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": {"RPG": 1.0}}


def test_note_request_accumulates_and_removes(df_table: DfTable) -> None:
    loaded(df_table)
    tools.note_request(like_games=["Hades"], boost_tags=["roguelike"], notes=["short sessions"])
    result = tools.note_request(like_games=["hades", "Celeste"], avoid_tags=["horror"], remove=["short sessions"])
    assert result["request_so_far"] == {
        "like_games": ["Hades", "Celeste"],
        "boost_tags": ["Roguelike"],
        "avoid_tags": ["Horror"],
        "notes": [],
    }


def test_note_request_reports_unknown_tags_without_noting_them(df_table: DfTable) -> None:
    loaded(df_table)
    result = tools.note_request(boost_tags=["Shoter"])
    assert result["unknown_tags_not_noted"] == {"Shoter": ["Shooter"]}
    assert tools.state["draft"]["boost_tags"] == []


def test_mcp_start_new_session_resets_the_draft(df_table: DfTable) -> None:
    loaded(df_table)
    tools.state["session"] = "old-session"
    tools.note_request(like_games=["Hades"], boost_tags=["Roguelike"])

    result = mcp_server.start_new_session()

    assert result["closed_session"] == "old-session"
    assert result["session"] == tools.state["session"] != "old-session"
    assert tools.state["draft"] == tools.empty_draft()
