from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

from src import cli, export

DAY = date(2026, 11, 1)
RESULT: dict[str, object] = {
    "applied": {
        "like_games": ["Portal 2"],
        "boost_tags": ["Puzzle"],
        "saved_boost_tags": [],
        "avoid_tags": ["Horror"],
        "from_saved_preferences": True,
        "player_notes": [],
    },
    "recommendations": [
        {
            "name": "Tom & Jerry | <b>Deluxe</b>",
            "appid": 620,
            "score": 0.912,
            "positive_ratio": 0.97,
            "matched_tags": ["Puzzle", "Co-op"],
            "price_usd": 7.49,
            "discount_percent": 75,
            "on_wishlist": True,
            "main_story_hours": 12.8,
        },
        {
            "name": "Plain",
            "appid": 400,
            "score": 0.5,
            "positive_ratio": None,
            "matched_tags": [],
            "price_usd": 0.0,
            "discount_percent": 0,
            "on_wishlist": False,
            "length_unknown": True,
        },
    ],
    "backlog": [
        {
            "name": "Owned",
            "appid": 70,
            "score": 0.4,
            "positive_ratio": None,
            "matched_tags": ["FPS"],
            "price_usd": None,
            "discount_percent": None,
            "playtime_hours": 0.5,
        }
    ],
    "wildcards": [
        {
            "name": "Cats & Dogs <3",
            "appid": 9,
            "score": 0.3,
            "positive_ratio": 0.88,
            "matched_tags": ["Cozy"],
            "price_usd": 19.99,
            "discount_percent": 0,
            "on_wishlist": False,
            "via_tag": "Cozy | Relaxing",
        }
    ],
}


GOLDEN = r"""# Gamer Guide picks, 2026-11-01

- **More like:** Portal 2
- **Must have:** Puzzle
- **Avoid:** Horror

## Recommended for you

| # | Game | Fit | Liked | Price | Story | Wishlist | Matched tags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | [Tom \& Jerry \| \<b\>Deluxe\</b\>](https://store.steampowered.com/app/620) | 0.91 | 97% | -75% $7.49 | 12.8h | ★ | Puzzle, Co-op |
| 2 | [Plain](https://store.steampowered.com/app/400) | 0.50 | - | free | ? |  |  |

## From your backlog

| # | Game | Fit | Hours | Matched tags |
| --- | --- | --- | --- | --- |
| 1 | [Owned](https://store.steampowered.com/app/70) | 0.40 | 0.5h | FPS |

## Wildcards: outside your usual tags

| # | Game | Fit | Liked | Price | Via tag | Matched tags |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [Cats \& Dogs \<3](https://store.steampowered.com/app/9) | 0.30 | 88% | $19.99 | Cozy \| Relaxing | Cozy |
"""


def test_markdown_golden() -> None:
    assert export.to_markdown(RESULT, DAY) == GOLDEN


def test_markdown_rows_keep_their_column_count() -> None:
    """An unescaped `|` in a name or tag would split its cell and shift every column after it."""
    for table in export.to_markdown(RESULT, DAY).split("## ")[1:]:
        rows = [line.replace(r"\|", "") for line in table.splitlines() if line.startswith("|")]
        assert len({row.count("|") for row in rows}) == 1


def test_wildcards_table_only_when_the_result_has_wildcards() -> None:
    assert "## Wildcards" in export.to_markdown(RESULT, DAY)
    for empty in ({}, {"wildcards": []}):
        assert "Wildcards" not in export.to_markdown({**RESULT, "wildcards": []} | empty, DAY)
    without = {key: value for key, value in RESULT.items() if key != "wildcards"}
    assert "Wildcards" not in export.to_markdown(without, DAY) and "Wildcards" not in export.to_html(without, DAY)


def test_known_fields_get_one_formatted_column_each() -> None:
    """Price reads like the terminal report: one cell, never the raw price_usd and discount_percent."""
    text = export.to_markdown(RESULT, DAY)
    for raw in (
        "Price usd",
        "Discount percent",
        "Main story hours",
        "Length unknown",
        "On wishlist",
        "| 7.49 |",
        "| 75 |",
    ):
        assert raw not in text
    assert "| -75% $7.49 |" in text


def test_wishlist_column_only_when_something_is_wishlisted() -> None:
    picks = [{"name": "A", "appid": 1, "score": 0.5, "positive_ratio": 0.8, "matched_tags": [], "on_wishlist": False}]
    assert "| # | Game | Fit | Liked | Matched tags |" in export.to_markdown({"recommendations": picks}, DAY)


def test_unknown_fields_become_generic_columns_when_present() -> None:
    picks = [{"name": "A", "appid": 1, "score": 0.5, "positive_ratio": 0.8, "matched_tags": [], "co_op": True}]
    text = export.to_markdown({"recommendations": picks}, DAY)
    assert "| # | Game | Fit | Liked | Co op | Matched tags |" in text
    assert "| 0.50 | 80% | yes |  |" in text


class Page(HTMLParser):
    """Collects what a browser would see: tags, link targets, text."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.links: dict[str, str] = {}
        self.text: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._href = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._href:
            self.links[data] = self._href


def test_html_is_self_contained_and_escaped() -> None:
    page = Page()
    page.feed(export.to_html(RESULT, DAY))
    assert "script" not in page.tags and "link" not in page.tags
    assert "b" not in page.tags  # the name's <b> stayed text
    assert page.links["Tom & Jerry | <b>Deluxe</b>"] == "https://store.steampowered.com/app/620"
    assert page.links["Owned"] == "https://store.steampowered.com/app/70"
    assert page.links["Cats & Dogs <3"] == "https://store.steampowered.com/app/9"
    assert page.tags.count("tr") == 1 + 2 + 1 + 1 + 1 + 1  # header + picks for recommendations, backlog, wildcards
    assert "-75% $7.49" in page.text and "Cozy | Relaxing" in page.text
    assert "Gamer Guide picks, 2026-11-01" in page.text


def test_export_unknown_extension_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ranking", lambda: pytest.fail("ranked before checking the extension"))
    target = tmp_path / "picks.pdf"
    with pytest.raises(SystemExit, match=r"\.md or \.html"):
        cli.export_report(target)
    assert not target.exists()


@pytest.mark.parametrize("name", ["picks.md", "out/picks.HTML"])
def test_export_writes_by_extension(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ranking", lambda: RESULT)
    target = tmp_path / name
    cli.export_report(target)
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Gamer Guide picks") if name.endswith(".md") else text.startswith("<!DOCTYPE html>")


def test_extra_field_empty_on_every_pick_gets_no_column() -> None:
    """Owned backlog games have no price: a column of dashes says nothing."""
    picks: list[dict[str, object]] = [
        {"name": n, "appid": 1, "score": 0.5, "playtime_hours": 0.0, "matched_tags": [], "price_usd": None, "x": None}
        for n in "AB"
    ]
    assert "| # | Game | Fit | Hours | Matched tags |" in export.to_markdown({"backlog": picks}, DAY)
    picks[1] |= {"price_usd": 9.99, "x": 1}
    text = export.to_markdown({"backlog": picks}, DAY)
    assert "| # | Game | Fit | Hours | Price | X | Matched tags |" in text
    assert "| 0.50 | 0.0h | - | - |  |" in text  # A: no price, no x
