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
