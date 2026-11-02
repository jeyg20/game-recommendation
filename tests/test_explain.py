import pytest

from src import tools
from src.explain import explain
from src.library import LibraryGame
from src.tag_profile import DfTable, cosine, norm, tag_vector
from src.tools import ReferenceGame, library_profile


def owned(appid: int, name: str, hours: float, tags: dict[str, int]) -> LibraryGame:
    return {
        "appid": appid,
        "name": name,
        "playtime_hours": hours,
        "main_story_hours": None,
        "review_score": None,
        "genres": None,
        "tags": tags,
    }


LIBRARY = [
    owned(1, "Dead Cells", 80, {"Roguelike": 50, "Action": 30, "Indie": 20}),
    owned(2, "Doom", 40, {"Shooter": 60, "Action": 40}),
    owned(3, "Portal", 10, {"Puzzle": 70, "Indie": 30}),
]
CANDIDATE = {"Roguelike": 40, "Action": 30, "Shooter": 20, "Horror": 10}  # Horror: in no owned game


def test_contributions_sum_to_the_score(df_table: DfTable) -> None:
    profile = library_profile(LIBRARY, df_table)
    why = explain(CANDIDATE, profile, df_table, LIBRARY)

    assert why["score"] == pytest.approx(cosine(profile, norm(profile), tag_vector(CANDIDATE, df_table)), abs=1e-12)
    total = sum(part["contribution"] for part in why["tags"]) + why["other_tags"]
    assert total == pytest.approx(why["score"], abs=1e-6)
    assert sum(part["share_of_score"] for part in why["tags"]) == pytest.approx(1.0, abs=1e-6)
    assert "Horror" not in [part["tag"] for part in why["tags"]]  # adds nothing: the profile doesn't have it
    contributions = [part["contribution"] for part in why["tags"]]
    assert contributions == sorted(contributions, reverse=True)


def test_because_you_played_names_the_owned_games_behind_the_top_tags(df_table: DfTable) -> None:
    profile = library_profile(LIBRARY, df_table)
    why = explain(CANDIDATE, profile, df_table, LIBRARY)

    top3 = [part["tag"] for part in why["tags"][:3]]
    assert set(top3) == {"Roguelike", "Action", "Shooter"}
    by_name = {game["name"]: game for game in why["because_you_played"]}
    assert set(by_name) == {"Dead Cells", "Doom"}  # Portal has none of the top tags
    assert set(by_name["Dead Cells"]["tags"]) == {"Roguelike", "Action"}
    assert set(by_name["Doom"]["tags"]) == {"Shooter", "Action"}
    assert by_name["Dead Cells"]["hours"] == 80


def test_no_shared_tags_explains_a_zero_score(df_table: DfTable) -> None:
    profile = library_profile(LIBRARY, df_table)
    why = explain({"Horror": 10}, profile, df_table, LIBRARY)
    assert why == {"score": 0.0, "tags": [], "other_tags": 0, "because_you_played": []}


def loaded_with(df_table: DfTable, monkeypatch: pytest.MonkeyPatch, found: ReferenceGame) -> None:
    games = {game["appid"]: game for game in LIBRARY}
    tools.state |= {
        "player_games": [],
        "games": games,
        "df_table": df_table,
        "profile": library_profile(games.values(), df_table),
    }
    monkeypatch.setattr(tools, "find_game", lambda name: found)


def test_explain_pick_marks_an_owned_game(df_table: DfTable, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_with(df_table, monkeypatch, {"appid": 1, "name": "Dead Cells", "tags": LIBRARY[0]["tags"] or {}})
    result = tools.explain_pick("dead cells")
    assert result["owned"] is True
    assert result["fit_score"] > 0.5
    assert "steered_by" not in result


def test_explain_pick_steered_uses_the_noted_draft(df_table: DfTable, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_with(df_table, monkeypatch, {"appid": 9, "name": "Shooty", "tags": {"Shooter": 80, "Co-op": 20}})
    plain = tools.explain_pick("shooty")
    tools.note_request(boost_tags=["co-op"], avoid_tags=["puzzle"])
    steered = tools.explain_pick("shooty", steered=True)

    assert plain["owned"] is False
    assert steered["steered_by"] == {"like_games": [], "boost_tags": ["Co-op"], "avoid_tags": ["Puzzle"]}
    assert "Co-op" not in [part["tag"] for part in plain["tags"]]  # type: ignore[union-attr]
    assert "Co-op" in [part["tag"] for part in steered["tags"]]  # type: ignore[union-attr]
    assert steered["fit_score"] > plain["fit_score"]  # type: ignore[operator]


def test_explain_pick_unknown_game(df_table: DfTable, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_with(df_table, monkeypatch, None)  # type: ignore[arg-type]
    assert "error" in tools.explain_pick("nope")
