import math

import pytest

from src.tag_profile import (
    DfTable,
    adjust_profile,
    build_profile,
    cosine,
    is_open_ended,
    norm,
    tag_vector,
    taste_weight,
    top_tags,
)


def test_open_ended_when_a_top_tag_is_multiplayer() -> None:
    assert is_open_ended({"Multiplayer": 50, "Action": 40})
    assert is_open_ended({"pvp": 10})  # case-insensitive


def test_not_open_ended_when_multiplayer_is_a_footnote() -> None:
    tags = {"RPG": 90, "Story Rich": 80, "Fantasy": 70, "Open World": 60, "Adventure": 50, "Multiplayer": 1}
    assert not is_open_ended(tags)


def test_taste_weight_mixes_completion_and_volume() -> None:
    volume = 1 - math.exp(-10 / 40)
    assert taste_weight(10, 20, {"RPG": 10}) == pytest.approx((0.5 + volume) / 2)


def test_taste_weight_caps_completion_at_one() -> None:
    volume = 1 - math.exp(-100 / 40)
    assert taste_weight(100, 10, {"RPG": 10}) == pytest.approx((1 + volume) / 2)


def test_taste_weight_is_volume_only_without_a_main_story() -> None:
    volume = 1 - math.exp(-10 / 40)
    assert taste_weight(10, None, {"RPG": 10}) == pytest.approx(volume)
    assert taste_weight(10, 0, {"RPG": 10}) == pytest.approx(volume)


def test_taste_weight_is_volume_only_for_open_ended_games() -> None:
    volume = 1 - math.exp(-10 / 40)
    assert taste_weight(10, 5, {"Multiplayer": 10}) == pytest.approx(volume)


def test_taste_weight_is_zero_when_never_played() -> None:
    assert taste_weight(0, 20, {"RPG": 10}) == 0


def test_tag_vector_is_vote_share_times_idf(df_table: DfTable) -> None:
    vector = tag_vector({"RPG": 30, "Indie": 10}, df_table)
    assert vector == pytest.approx({"RPG": 0.75 * df_table["idf"]["RPG"], "Indie": 0.25 * df_table["idf"]["Indie"]})


def test_tag_vector_gives_unseen_tags_the_df_zero_idf(df_table: DfTable) -> None:
    vector = tag_vector({"Brand New Tag": 5}, df_table)
    assert vector == pytest.approx({"Brand New Tag": math.log(1 + df_table["n"]) + 1})


def test_tag_vector_of_untagged_game_is_empty(df_table: DfTable) -> None:
    assert tag_vector({}, df_table) == {}


def test_build_profile_sums_weighted_vectors_and_skips_zero_weights(df_table: DfTable) -> None:
    profile = build_profile([(0.5, {"RPG": 1}), (1.0, {"RPG": 1, "Indie": 1}), (0.0, {"Horror": 1})], df_table)
    rpg, indie = df_table["idf"]["RPG"], df_table["idf"]["Indie"]
    assert profile == pytest.approx({"RPG": 0.5 * rpg + 0.5 * rpg, "Indie": 0.5 * indie})
    assert "Horror" not in profile


def test_build_profile_negative_weight_counts_against_tags_and_drops_nonpositive(df_table: DfTable) -> None:
    profile = build_profile([(1.0, {"RPG": 1, "Indie": 1}), (-0.3, {"RPG": 1}), (-0.3, {"Horror": 1})], df_table)
    rpg, indie = df_table["idf"]["RPG"], df_table["idf"]["Indie"]
    assert profile == pytest.approx({"RPG": 0.5 * rpg - 0.3 * rpg, "Indie": 0.5 * indie})
    assert "Horror" not in profile  # only an abandoned game had it: below zero, dropped


def test_adjust_profile_without_steers_is_the_unit_profile() -> None:
    adjusted = adjust_profile({"RPG": 3.0, "Indie": 4.0}, [], [], [])
    assert adjusted == pytest.approx({"RPG": 0.6, "Indie": 0.8})


def test_adjust_profile_adds_unit_references_sharing_the_weight() -> None:
    adjusted = adjust_profile({"RPG": 1.0}, [{"Puzzle": 2.0}, {"Horror": 5.0}], [], [])
    assert adjusted == pytest.approx({"RPG": 1.0, "Puzzle": 0.5, "Horror": 0.5})


def test_adjust_profile_boosts_by_the_strongest_tag() -> None:
    adjusted = adjust_profile({"RPG": 3.0, "Indie": 4.0}, [], ["Shooter", "RPG"], [])
    assert adjusted == pytest.approx({"RPG": 0.6 + 0.8, "Indie": 0.8, "Shooter": 0.8})


def test_adjust_profile_drops_avoided_tags_case_insensitively() -> None:
    adjusted = adjust_profile({"RPG": 3.0, "Horror": 4.0}, [{"horror": 1.0}], [], ["HORROR"])
    assert set(adjusted) == {"RPG"}


def test_adjust_profile_of_empty_profile_still_takes_boosts() -> None:
    assert adjust_profile({}, [], ["Shooter"], []) == {"Shooter": 1.0}


def test_top_tags_strongest_first() -> None:
    assert top_tags({"a": 0.1, "b": 0.9, "c": 0.5}, 2) == ["b", "c"]


def test_cosine_of_same_direction_is_one() -> None:
    profile = {"RPG": 1.0, "Indie": 2.0}
    assert cosine(profile, norm(profile), {"RPG": 2.0, "Indie": 4.0}) == pytest.approx(1.0)


def test_cosine_of_disjoint_vectors_is_zero() -> None:
    profile = {"RPG": 1.0}
    assert cosine(profile, norm(profile), {"Horror": 1.0}) == 0.0


def test_cosine_of_empty_vectors_is_zero() -> None:
    assert cosine({}, 0.0, {"RPG": 1.0}) == 0.0
    assert cosine({"RPG": 1.0}, 1.0, {}) == 0.0


def test_adjust_profile_subtracts_unit_negatives_sharing_the_weight() -> None:
    adjusted = adjust_profile({"RPG": 3.0, "Indie": 4.0}, [], [], [], negatives=[{"Indie": 2.0}, {"RPG": 1.0}])
    assert adjusted == pytest.approx({"RPG": 0.6 - 0.25, "Indie": 0.8 - 0.25})


def test_adjust_profile_drops_tags_negatives_push_to_zero_or_below() -> None:
    adjusted = adjust_profile({"RPG": 0.3, "Indie": 1.0}, [], [], [], negatives=[{"RPG": 1.0, "Horror": 1.0}])
    assert set(adjusted) == {"Indie"}  # RPG went below zero, Horror was never there
