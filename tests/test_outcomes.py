from pathlib import Path

from src import tools
from src.history import ToolCall, log_turn
from src.library import LibraryGame
from src.outcomes import pick_outcomes
from src.tag_profile import DfTable


def owned(appid: int, name: str, hours: float) -> LibraryGame:
    return {
        "appid": appid,
        "name": name,
        "playtime_hours": hours,
        "main_story_hours": None,
        "review_score": None,
        "genres": None,
        "tags": {"Action": 10},
    }


def pick(appid: int, name: str, score: float) -> dict[str, object]:
    return {"name": name, "appid": appid, "score": score, "positive_ratio": 0.9, "matched_tags": ["Action"]}


def recommend_call(picks: list[dict[str, object]], backlog: list[dict[str, object]]) -> ToolCall:
    return {"name": "recommend_games", "args": {}, "result": {"recommendations": picks, "backlog": backlog}}


# the library now: Hades bought and played a lot, Celeste bought but barely touched, the backlog game started
LIBRARY = {
    10: owned(10, "Hades", 30.0),
    20: owned(20, "Celeste", 0.5),
    50: owned(50, "Old Backlog", 4.0),
    60: owned(60, "Untouched Backlog", 0.13),
}


def write_two_sessions() -> None:
    log_turn(
        "2026-10-01_10-00-00",
        "recommend",
        [
            recommend_call(
                [pick(10, "Hades", 0.7), pick(30, "Doom", 0.6)],
                [{**pick(50, "Old Backlog", 0.5), "playtime_hours": 0.0}],
            )
        ],
        "",
    )
    log_turn(
        "2026-10-02_10-00-00",
        "again",
        [
            {"name": "look_up_game", "args": {}, "result": {"name": "Doom"}},
            recommend_call(
                [pick(30, "Doom", 0.65), pick(20, "Celeste", 0.6), pick(10, "Hades", 0.5)],
                [{**pick(60, "Untouched Backlog", 0.4), "playtime_hours": 0.1}],
            ),
            {"name": "recommend_games", "args": {}, "result": None, "error": "boom"},
        ],
        "",
    )


def test_each_game_counts_once_with_its_first_rank_and_score() -> None:
    write_two_sessions()
    outcomes = pick_outcomes(LIBRARY)

    by_appid = {p["appid"]: p for p in outcomes["picks"]}
    assert [p["appid"] for p in outcomes["picks"]] == [10, 30, 20]  # oldest first, no repeats
    assert by_appid[10]["rank"] == 1 and by_appid[10]["score"] == 0.7  # first pick, not the later rank 3
    assert by_appid[10]["session"] == "2026-10-01_10-00-00"
    assert by_appid[30]["bought"] is False and by_appid[30]["hours"] == 0.0
    assert by_appid[20]["bought"] is True and by_appid[20]["rank"] == 2

    backlog = {b["appid"]: b for b in outcomes["backlog"]}
    assert backlog[50]["started"] is True
    assert backlog[60]["started"] is False  # 0.13 h logged as 0.1 h: no new playtime


def test_tool_summarises_and_lists_bought_games_best_played_first(df_table: DfTable, isolated_data: Path) -> None:
    write_two_sessions()
    tools.state |= {"player_games": [], "games": LIBRARY, "df_table": df_table, "profile": {"Action": 1.0}}
    before = sorted(p.relative_to(isolated_data) for p in isolated_data.rglob("*"))

    result = tools.get_pick_outcomes()

    assert result["summary"] == {
        "recommended": 3,
        "bought": 2,
        "played_over_2h": 1,
        "buy_rate": 0.667,
        "hit_rate": 0.333,
        "backlog_recommended": 2,
        "backlog_started": 1,
    }
    assert [g["name"] for g in result["bought_games"]] == ["Hades", "Celeste"]
    assert "last library refresh" in str(result["note"])
    assert sorted(p.relative_to(isolated_data) for p in isolated_data.rglob("*")) == before  # read-only


def test_no_sessions_gives_an_empty_summary(df_table: DfTable) -> None:
    tools.state |= {"player_games": [], "games": LIBRARY, "df_table": df_table, "profile": {"Action": 1.0}}
    result = tools.get_pick_outcomes()
    assert result["summary"] == {
        "recommended": 0,
        "bought": 0,
        "played_over_2h": 0,
        "buy_rate": None,
        "hit_rate": None,
        "backlog_recommended": 0,
        "backlog_started": 0,
    }
    assert result["bought_games"] == []


def test_a_bought_pick_that_comes_back_as_backlog_counts_only_as_a_purchase() -> None:
    log_turn("2026-10-01_10-00-00", "r", [recommend_call([pick(10, "Hades", 0.7)], [])], "")
    log_turn("2026-10-02_10-00-00", "r", [recommend_call([], [{**pick(10, "Hades", 0.5), "playtime_hours": 0.0}])], "")
    outcomes = pick_outcomes(LIBRARY)
    assert [p["appid"] for p in outcomes["picks"]] == [10]
    assert outcomes["backlog"] == []
