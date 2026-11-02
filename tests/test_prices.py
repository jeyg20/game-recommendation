from typing import cast

import pytest

from src import recommend as rec
from src import tools
from src.recommend import parse_int, price_usd
from src.steamspy import GameSummary
from src.tag_profile import CorpusRecord, DfTable
from src.ui import price, recommendation_tables, table


def listing(appid: int, price: str = "", discount: str = "") -> GameSummary:
    """A SteamSpy tag-listing entry with SteamSpy's string price fields (US cents, percent)."""
    return cast(
        GameSummary,
        {
            "appid": appid,
            "name": f"Game {appid}",
            "positive": 900,
            "negative": 100,
            "price": price,
            "initialprice": price,
            "discount": discount,
        },
    )


@pytest.mark.parametrize(
    ("value", "expected"), [("2999", 2999), ("0", 0), ("50", 50), ("", None), ("abc", None), ("12.5", None)]
)
def test_parse_int(value: str, expected: int | None) -> None:
    assert parse_int(value) == expected


def test_price_usd_converts_cents_and_tolerates_missing() -> None:
    assert price_usd(listing(1, price="2999")) == pytest.approx(29.99)
    assert price_usd(listing(1, price="0")) == 0.0
    assert price_usd(listing(1, price="")) is None
    assert price_usd(listing(1, price="n/a")) is None
    assert price_usd(cast(GameSummary, {"appid": 1})) is None  # field missing altogether


PRICES = {1: ("2999", "0"), 2: ("1499", "50"), 3: ("0", "0"), 4: ("", ""), 5: ("999", "75")}


@pytest.fixture
def priced_pools(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> None:
    """One RPG pool of five games with the prices above; every game has the same tags, so the order is stable."""

    def fetch_tag_pools(tags: list[str]) -> tuple[dict[str, dict[str, GameSummary]], dict[str, str]]:
        pool = {str(appid): listing(appid, *PRICES[appid]) for appid in PRICES}
        return {"RPG": pool}, {}

    def enrich_tags(appids: list[int]) -> dict[int, CorpusRecord]:
        return {a: {"appid": a, "genre": "", "tags": {"RPG": 10}, "fetched_at": "", "status": "ok"} for a in appids}

    monkeypatch.setattr(rec, "fetch_tag_pools", fetch_tag_pools)
    monkeypatch.setattr(rec, "enrich_tags", enrich_tags)
    tools.state |= {"player_games": [], "games": {}, "df_table": df_table, "profile": {"RPG": 1.0}}


def test_recommend_fills_prices_from_the_listing(priced_pools: None, df_table: DfTable) -> None:
    result = rec.recommend({"RPG": 1.0}, ["RPG"], set(), df_table)
    prices = {r["appid"]: (r["price_usd"], r["discount_percent"]) for r in result["recommendations"]}
    assert prices == {1: (29.99, 0), 2: (14.99, 50), 3: (0.0, 0), 4: (None, None), 5: (9.99, 75)}


def test_backlog_picks_have_no_price(df_table: DfTable) -> None:
    [pick] = rec.score_backlog([(1, "Owned", {"RPG": 10})], {"RPG": 1.0}, df_table)
    assert pick["price_usd"] is None and pick["discount_percent"] is None


def test_recommend_games_passes_prices_through(priced_pools: None) -> None:
    picks = cast(list[dict[str, object]], tools.recommend_games(include_backlog=False)["recommendations"])
    assert {(p["appid"], p["price_usd"], p["discount_percent"]) for p in picks} == {
        (1, 29.99, 0),
        (2, 14.99, 50),
        (3, 0.0, 0),
        (4, None, None),
        (5, 9.99, 75),
    }


def test_on_sale_only_keeps_discounted_picks_before_the_limit(priced_pools: None) -> None:
    result = tools.recommend_games(include_backlog=False, on_sale_only=True)
    picks = cast(list[dict[str, object]], result["recommendations"])
    assert {p["appid"] for p in picks} == {2, 5}  # fewer than limit
    one = tools.recommend_games(include_backlog=False, on_sale_only=True, limit=1)
    assert [p["appid"] for p in cast(list[dict[str, object]], one["recommendations"])] in ([2], [5])


@pytest.mark.parametrize(
    ("usd", "discount", "expected"),
    [(29.99, 0, "$29.99"), (14.99, 50, "-50% $14.99"), (0.0, 0, "free"), (None, None, "-")],
)
def test_price_cell(usd: float | None, discount: int | None, expected: str) -> None:
    assert price({"price_usd": usd, "discount_percent": discount}) == expected


def test_tables_show_price_for_recommendations_only() -> None:
    pick = {"name": "A", "appid": 1, "score": 0.5, "positive_ratio": 0.9, "matched_tags": ["RPG"]}
    result = {
        "recommendations": [pick | {"price_usd": 14.99, "discount_percent": 50}],
        "backlog": [pick | {"price_usd": None, "discount_percent": None, "playtime_hours": 0.5}],
    }
    recommended, backlog = recommendation_tables(result).split("From your backlog")
    assert "Price" in recommended and "-50% $14.99" in recommended
    assert "Price" not in backlog
    assert "Price" not in table("T", [pick], "Liked", "positive_ratio")  # a pick without price fields still renders
