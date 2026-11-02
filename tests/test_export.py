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
        },
        {"name": "Plain", "appid": 400, "score": 0.5, "positive_ratio": None, "matched_tags": []},
    ],
    "backlog": [
        {
            "name": "Owned",
            "appid": 70,
            "score": 0.4,
            "positive_ratio": None,
            "matched_tags": ["FPS"],
            "playtime_hours": 0.5,
        }
    ],
}


GOLDEN = r"""# Gamer Guide picks, 2026-11-01

- **More like:** Portal 2
- **Must have:** Puzzle
- **Avoid:** Horror

## Recommended for you

| # | Game | Fit | Liked | Matched tags |
| --- | --- | --- | --- | --- |
| 1 | [Tom \& Jerry \| \<b\>Deluxe\</b\>](https://store.steampowered.com/app/620) | 0.91 | 97% | Puzzle, Co-op |
| 2 | [Plain](https://store.steampowered.com/app/400) | 0.50 | - |  |

## From your backlog

| # | Game | Fit | Hours | Matched tags |
| --- | --- | --- | --- | --- |
| 1 | [Owned](https://store.steampowered.com/app/70) | 0.40 | 0.5h | FPS |
"""


def test_markdown_golden() -> None:
    assert export.to_markdown(RESULT, DAY) == GOLDEN


def test_markdown_rows_keep_their_column_count() -> None:
    """An unescaped `|` in a name would split its cell and shift every column after it."""
    rows = [line for line in export.to_markdown(RESULT, DAY).splitlines() if line.startswith("|")]
    for row in rows:
        assert row.replace(r"\|", "").count("|") == 6


def test_extra_fields_become_columns_when_present() -> None:
    picks = [{"name": "A", "appid": 1, "score": 0.5, "positive_ratio": 0.8, "matched_tags": [], "on_wishlist": True}]
    text = export.to_markdown({"recommendations": picks}, DAY)
    assert "| # | Game | Fit | Liked | On wishlist | Matched tags |" in text
    assert "| 0.50 | 80% | yes |  |" in text
    assert "On wishlist" not in export.to_markdown(RESULT, DAY)


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
    assert page.tags.count("tr") == 1 + 2 + 1 + 1  # header + picks, header + backlog
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
