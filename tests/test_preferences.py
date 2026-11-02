from src.preferences import merge, without


def test_merge_keeps_first_spelling_and_dedupes_case_insensitively() -> None:
    assert merge(["RPG", "Indie"], ["rpg", "Horror"]) == ["RPG", "Indie", "Horror"]


def test_merge_with_nothing_extra() -> None:
    assert merge(["RPG"], None) == ["RPG"]


def test_without_drops_case_insensitively() -> None:
    assert without(["RPG", "Indie", "Horror"], ["indie", "horror"]) == ["RPG"]


def test_without_nothing_removed() -> None:
    assert without(["RPG"], None) == ["RPG"]
