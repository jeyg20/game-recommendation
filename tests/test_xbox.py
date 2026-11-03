"""Xbox games: OpenXBL titles matched to Steam by name, counted in the profile by hours and never recommended."""

from datetime import datetime, timedelta
from typing import Literal, cast

import pytest
import requests

from src import config, library, openxbl, steam_api, tools, xbox
from src.library import LibraryGame
from src.openxbl import XboxTitle
from src.steam_api import OwnedGame, StoreSearchItem
from src.storage import read_json, write_json
from src.tag_profile import CorpusRecord, DfTable, build_profile, taste_weight
from tests.test_wildcards import GAMES, PROFILE, fake_pools, wide_df_table  # noqa: F401 - fixtures

FAKE_KEY = "not-a-real-openxbl-key"


def title(
    title_id: str, name: str, progress: float = 0.0, kind: str = "Game", devices: tuple[str, ...] = ("Win32",)
) -> XboxTitle:
    return {"titleId": title_id, "name": name, "type": kind, "devices": list(devices), "progress": progress}


TITLES = [
    title("1", "Steam Dup", 50),  # matched, but the Steam library has it
    title("2", "Some Game Demo", 90),  # a demo: dropped before matching
    title("3", "Obscure Thing", 10),  # the store only has a sequel: no match
    title("4", "Xbox Only Quest", 40),  # matched, Xbox only: the one game in the profile
]
STORE: dict[str, list[StoreSearchItem]] = {
    "Steam Dup": [{"id": 100, "name": "Steam Dup", "type": "app"}],
    "Obscure Thing": [{"id": 300, "name": "Obscure Thing 2", "type": "app"}],
    "Xbox Only Quest": [
        {"id": 201, "name": "Xbox Only Quest Soundtrack", "type": "dlc"},
        {"id": 200, "name": "Xbox Only Quest™", "type": "app"},
    ],
}
TAGS = {100: {"Shooter": 10}, 200: {"RPG": 10, "Co-op": 4}}
MINUTES: dict[str, int | None] = {"1": 10 * 60, "3": None, "4": 75 * 60}  # titleId -> MinutesPlayed (None: no stat)


class FakeServices:
    """OpenXBL, the store search and the tag fetch, counting calls. `titles = None` makes OpenXBL fail, a `minutes`
    value of False makes that title's minutes request fail."""

    def __init__(self) -> None:
        self.titles: list[XboxTitle] | None = list(TITLES)
        self.title_calls = 0
        self.searches: list[str] = []
        self.minutes: dict[str, int | None | Literal[False]] = dict(MINUTES)
        self.minutes_asked: list[str] = []

    def get_titles(self) -> list[XboxTitle] | None:
        self.title_calls += 1
        return self.titles

    def get_minutes_played(self, title_id: str) -> int | None | Literal[False]:
        self.minutes_asked.append(title_id)
        return self.minutes.get(title_id)

    def search(self, term: str) -> list[StoreSearchItem] | None:
        self.searches.append(term)
        return STORE.get(term, [])

    def enrich_tags(self, appids: list[int]) -> dict[int, CorpusRecord]:
        return {a: {"appid": a, "genre": "", "tags": TAGS[a], "fetched_at": "", "status": "ok"} for a in appids}


STEAM_GAME: LibraryGame = {
    "appid": 100,
    "name": "Steam Dup",
    "playtime_hours": 30.0,
    "main_story_hours": None,
    "review_score": None,
    "genres": None,
    "tags": TAGS[100],
}


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch, df_table: DfTable) -> FakeServices:
    fake = FakeServices()
    monkeypatch.setattr(openxbl, "get_titles", fake.get_titles)
    monkeypatch.setattr(openxbl, "get_minutes_played", fake.get_minutes_played)
    monkeypatch.setattr(steam_api, "try_search_store", fake.search)
    monkeypatch.setattr(xbox, "enrich_tags", fake.enrich_tags)
    monkeypatch.setattr(xbox.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(library, "load_achievement_shares", lambda player_games: {})
    owned = cast(
        list[OwnedGame], [{"appid": 100, "name": "Steam Dup", "playtime_forever": 1800, "rtime_last_played": 0}]
    )
    tools.state |= {"player_games": owned, "games": {100: STEAM_GAME}, "df_table": df_table}
    return fake


def test_only_the_matched_xbox_only_game_enters_the_profile_by_its_hours(
    services: FakeServices, df_table: DfTable
) -> None:
    steam_only = tools.ensure_loaded()[3]
    result = tools.refresh_xbox()

    assert result["titles"] == 3 and result["matched"] == 2 and result["tagged"] == 1
    assert result["unmatched"] == ["Obscure Thing"] and result["skipped_steam_duplicates"] == 1
    assert "Some Game Demo" not in services.searches
    assert tools.state["xbox"] is not None
    [game] = tools.state["xbox"]["games"]
    assert game["appid"] == 200 and game["weight"] == taste_weight(75, None, TAGS[200])
    steam_weight = cast(dict[int, float], tools.state["weights"])[100]
    expected = build_profile([(steam_weight, TAGS[100]), (game["weight"], TAGS[200])], df_table)
    assert tools.state["profile"] == expected != steam_only
    profile = tools.get_taste_profile()
    assert profile["xbox_games"] == 1
    assert profile["xbox_most_played"] == [
        {"name": "Xbox Only Quest", "hours": 75.0},
        {"name": "Steam Dup", "hours": 10.0},
    ]


def test_xbox_hours_add_to_the_same_game_on_steam(services: FakeServices) -> None:
    steam_alone = tools.library_weights([STEAM_GAME])[100]
    tools.refresh_xbox()
    both = cast(dict[int, float], tools.state["weights"])[100]
    assert both == tools.library_weights([STEAM_GAME | {"playtime_hours": 40.0}])[100] > steam_alone


def test_a_game_without_minutes_weighs_nothing_but_is_still_never_recommended(services: FakeServices) -> None:
    services.minutes["4"] = None
    tools.refresh_xbox()
    assert tools.state["xbox"] is not None
    [game] = tools.state["xbox"]["games"]
    assert game["appid"] == 200 and game["weight"] == 0
    assert 200 in tools.xbox_appids()
    assert tools.get_taste_profile()["xbox_games"] == 0


def test_only_played_titles_are_asked_for_minutes(services: FakeServices) -> None:
    services.titles = [*TITLES, title("5", "Catalogue Entry"), title("6", "Console Game", devices=("XboxSeries",))]
    tools.refresh_xbox()
    assert sorted(services.minutes_asked) == ["1", "3", "4", "6"]  # not the 0% PC entry, not the demo


def test_every_copy_of_a_game_is_asked_and_the_one_with_hours_counts(services: FakeServices) -> None:
    pc_copy = title("4", "Xbox Only Quest")  # the smallest id is kept, but the hours are on the console copy
    services.titles = [pc_copy, title("9", "XBOX ONLY QUEST™", 40, devices=("XboxSeries",))]
    services.minutes = {"4": None, "9": 75 * 60}
    result = tools.refresh_xbox()
    assert sorted(services.minutes_asked) == ["4", "9"] and result["total_hours"] == 75
    assert tools.state["xbox"] is not None
    assert tools.state["xbox"]["games"][0]["weight"] == taste_weight(75, None, TAGS[200])


def test_a_cached_null_is_not_asked_again_within_a_week_but_an_old_entry_is(services: FakeServices) -> None:
    first = tools.refresh_xbox()
    assert first["hours_requests"] == 3
    assert cast(dict[str, dict[str, object]], read_json(config.XBOX_MINUTES_PATH))["3"]["minutes"] is None
    services.minutes_asked.clear()
    assert tools.refresh_xbox()["hours_requests"] == 0 and services.minutes_asked == []

    cache = cast(dict[str, dict[str, object]], read_json(config.XBOX_MINUTES_PATH))
    cache["3"]["fetched_at"] = (datetime.now() - xbox.MINUTES_MAX_AGE - timedelta(days=1)).isoformat()
    write_json(config.XBOX_MINUTES_PATH, cache)
    tools.refresh_xbox()
    assert services.minutes_asked == ["3"]


def test_a_failed_minutes_request_is_not_cached_and_stops_the_round(services: FakeServices) -> None:
    services.titles = [title("4", "Xbox Only Quest", 40), title("1", "Steam Dup", 50)]
    services.minutes["4"] = False
    result = tools.refresh_xbox()
    assert "hours_failed" in result and result["hours_requests"] == 1 and services.minutes_asked == ["4"]
    assert not config.XBOX_MINUTES_PATH.exists()
    services.minutes["4"] = 75 * 60
    services.minutes_asked.clear()
    tools.refresh_xbox()
    assert sorted(services.minutes_asked) == ["1", "4"]


def test_one_refresh_makes_at_most_the_capped_requests_oldest_first(
    services: FakeServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(xbox, "MINUTES_MAX_REQUESTS", 2)
    tools.refresh_xbox()
    assert len(services.minutes_asked) == 2
    services.minutes_asked.clear()
    tools.refresh_xbox()
    assert len(services.minutes_asked) == 1  # the one left; the two read are a week from due

    cache = cast(dict[str, dict[str, object]], read_json(config.XBOX_MINUTES_PATH))
    stale = datetime.now() - xbox.MINUTES_MAX_AGE - timedelta(days=1)
    for n, title_id in enumerate(["1", "3", "4"]):
        cache[title_id]["fetched_at"] = (stale - timedelta(days=n)).isoformat()
    write_json(config.XBOX_MINUTES_PATH, cache)
    services.minutes_asked.clear()
    tools.refresh_xbox()
    assert services.minutes_asked == ["4", "3"]


def test_most_played_is_sorted_by_hours_and_capped(services: FakeServices) -> None:
    services.titles = [title(str(n), f"Console Game {n}", devices=("XboxOne",)) for n in range(12)]
    services.minutes = {str(n): (n * 7 % 12 + 1) * 60 for n in range(12)}
    result = tools.refresh_xbox()
    hours = [game["hours"] for game in cast(list[dict[str, float]], result["most_played"])]
    assert len(hours) == 10 and hours == sorted(hours, reverse=True) and hours[0] == 12
    assert result["total_hours"] == sum(range(1, 13))


def test_a_miss_is_cached_and_not_looked_up_again(services: FakeServices) -> None:
    tools.refresh_xbox()
    first = list(services.searches)
    services.searches.clear()
    result = tools.refresh_xbox()
    assert sorted(first) == ["Obscure Thing", "Steam Dup", "Xbox Only Quest"]
    assert services.searches == [] and result["new_matches"] == 0
    assert cast(dict[str, object], read_json(config.XBOX_MATCHES_PATH))["3"] == {
        "appid": None,
        "steam_name": None,
        "matched_at": datetime.now().date().isoformat(),
    }


def test_an_old_miss_is_asked_again(services: FakeServices) -> None:
    tools.refresh_xbox()
    old = (datetime.now() - xbox.MATCH_MAX_AGE - timedelta(days=1)).date().isoformat()
    matches = cast(dict[str, dict[str, object]], read_json(config.XBOX_MATCHES_PATH))
    matches["3"]["matched_at"] = old
    write_json(config.XBOX_MATCHES_PATH, matches)
    services.searches.clear()
    tools.refresh_xbox()
    assert services.searches == ["Obscure Thing"]


def test_a_failed_store_search_is_not_cached_as_a_miss(services: FakeServices, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steam_api, "try_search_store", lambda term: None)
    assert tools.refresh_xbox()["matched"] == 0
    monkeypatch.setattr(steam_api, "try_search_store", services.search)
    tools.refresh_xbox()
    assert sorted(services.searches) == ["Obscure Thing", "Steam Dup", "Xbox Only Quest"]


def test_openxbl_failing_keeps_the_old_cache_and_says_so(services: FakeServices) -> None:
    tools.refresh_xbox()
    cached = read_json(config.XBOX_TITLES_PATH)
    services.titles = None
    result = tools.refresh_xbox()
    assert "openxbl_failed" in result and result["tagged"] == 1
    assert read_json(config.XBOX_TITLES_PATH) == cached


def test_refresh_without_a_key_or_cache_is_an_error_not_an_exception(services: FakeServices) -> None:
    services.titles = None
    assert "error" in tools.refresh_xbox()
    assert tools.state["xbox"] is None


def test_nothing_is_fetched_until_xbox_is_set_up(services: FakeServices) -> None:
    tools.ensure_loaded()
    assert services.title_calls == 0 and services.searches == [] and services.minutes_asked == []
    profile = tools.get_taste_profile()
    assert profile["xbox_games"] == 0 and profile["xbox_most_played"] == []


def test_loading_the_profile_reads_minutes_from_the_cache_only(services: FakeServices) -> None:
    tools.refresh_xbox()
    cache = cast(dict[str, dict[str, object]], read_json(config.XBOX_MINUTES_PATH))
    old = (datetime.now() - xbox.MINUTES_MAX_AGE - timedelta(days=1)).isoformat()
    write_json(config.XBOX_MINUTES_PATH, {key: entry | {"fetched_at": old} for key, entry in cache.items()})
    services.minutes_asked.clear()
    tools.state |= {"xbox": None, "profile": None}
    tools.ensure_loaded()
    assert services.minutes_asked == [] and tools.state["xbox"] is not None
    assert tools.state["xbox"]["games"][0]["weight"] == taste_weight(75, None, TAGS[200])


def test_a_fresh_cache_is_read_without_a_request(services: FakeServices) -> None:
    tools.refresh_xbox()
    services.title_calls = 0
    tools.state |= {"xbox": None, "profile": None}
    tools.ensure_loaded()
    assert services.title_calls == 0 and tools.state["xbox"] is not None


def test_a_day_old_cache_is_refetched_when_the_profile_loads(services: FakeServices) -> None:
    tools.refresh_xbox()
    cache = cast(dict[str, object], read_json(config.XBOX_TITLES_PATH))
    write_json(config.XBOX_TITLES_PATH, cache | {"fetched_at": (datetime.now() - timedelta(days=2)).isoformat()})
    services.title_calls = 0
    tools.state |= {"xbox": None, "profile": None}
    tools.ensure_loaded()
    assert services.title_calls == 1


@pytest.fixture
def owned_on_xbox(fake_pools: list[str], wide_df_table: DfTable) -> None:  # noqa: F811 - fixture param
    """The wildcard tests' library and pools, plus a never-launched Steam game (5) that's backlog."""
    owned = [{"appid": 13, "name": "Game 13", "playtime_forever": 600, "rtime_last_played": 0}]
    backlog: LibraryGame = STEAM_GAME | {"appid": 5, "name": "Game 5", "playtime_hours": 0.0, "tags": GAMES[10][0]}
    tools.state |= {"player_games": owned, "games": {5: backlog}, "df_table": wide_df_table, "profile": PROFILE}


def picked(result: dict[str, object], key: str) -> set[object]:
    return {pick["appid"] for pick in cast(list[dict[str, object]], result[key])}


def test_a_game_owned_on_xbox_is_never_recommended(owned_on_xbox: None) -> None:
    before = tools.recommend_games()
    assert 1 in picked(before, "recommendations") and 10 in picked(before, "wildcards")
    assert 5 in picked(before, "backlog")

    tools.state["xbox"] = {
        "games": [],
        "appids": [1, 5, 10],
        "titles": 3,
        "matched": 3,
        "unmatched": [],
        "steam_duplicates": 1,
        "steam_hours": {},
        "played": [],
        "total_hours": 0.0,
        "minutes_requests": 0,
        "minutes_failed": False,
        "new_matches": 0,
        "fetch_failed": False,
        "fetched_at": "",
    }
    after = tools.recommend_games()
    for key in ("recommendations", "wildcards", "backlog"):
        assert not {1, 5, 10} & picked(after, key)


def test_a_store_labelled_name_is_searched_again_cleaned(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[str, list[StoreSearchItem]] = {
        "cities skylines ii": [{"id": 7, "name": "Cities: Skylines II", "type": "app"}]
    }
    asked: list[str] = []
    monkeypatch.setattr(steam_api, "try_search_store", lambda term: asked.append(term) or store.get(term, []))
    monkeypatch.setattr(xbox.time, "sleep", lambda seconds: None)
    hit = xbox.search("Cities: Skylines II - PC Edition")
    assert hit and hit["id"] == 7 and asked == ["Cities: Skylines II - PC Edition", "cities skylines ii"]
    asked.clear()
    assert xbox.search("Hades") is None and asked == ["Hades"]  # nothing to clean: one search


def test_matching_is_strict() -> None:
    def match(name: str, *store_names: str) -> int | None:
        items: list[StoreSearchItem] = [{"id": n, "name": s, "type": "app"} for n, s in enumerate(store_names)]
        hit = xbox.best_match(name, items)
        return hit["id"] if hit else None

    assert match("Forza Horizon 5", "Forza Horizon 4") is None
    assert match("The Elder Scrolls IV: Oblivion Remastered", "The Elder Scrolls V: Skyrim") is None
    assert match("Hades", "Hades II", "Hades") == 1
    assert match("The Witcher 3: Wild Hunt – Complete Edition", "The Witcher 3: Wild Hunt") == 0
    assert match("DOOM Eternal", "DOOM Eternal ") == 0
    assert match("Starfield", "Stardew Valley") is None
    # Steam renamed some games for a re-release: the same game, unless the numbers differ
    assert (
        match(
            "The Witcher 3: Wild Hunt",
            "The Witcher 3: Wild Hunt — Remastered Soundtrack",
            "The Witcher 3: Wild Hunt — Remastered",
        )
        == 1
    )
    assert match("Death Stranding", "DEATH STRANDING 2: ON THE BEACH", "DEATH STRANDING DIRECTOR'S CUT") == 1
    assert match("Grand Theft Auto V Enhanced (PC)", "Grand Theft Auto V Enhanced") == 0
    assert match("Oblivion Remastered", "Oblivion", "Oblivion Remastered") == 1  # identical beats re-release


def test_demos_apps_and_copies_collapse_to_one_game_each() -> None:
    titles = [
        title("9", "Halo Infinite", 10),
        title("8", "HALO INFINITE™", 60),
        title("7", "Cursed Companions Demo"),
        title("6", "Microsoft Minesweeper", kind="App"),
        title("5", "Big Game Playtest"),
    ]
    [game] = xbox.game_titles(titles)
    assert game["titleId"] == "8" and game["progress"] == 60


def test_no_key_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "XBOX_API_KEY", "")
    assert openxbl.get_titles() is None  # the conftest blocks HTTP: a request would raise


def test_the_key_goes_in_the_header_and_never_in_a_failure_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sent: dict[str, object] = {}

    def get(url: str, headers: dict[str, str], timeout: float) -> requests.Response:
        sent.update(url=url, headers=headers)
        raise requests.ConnectionError(f"failed {url} {headers}")

    monkeypatch.setattr(config, "XBOX_API_KEY", FAKE_KEY)
    monkeypatch.setattr(requests, "get", get)
    assert openxbl.get_titles() is None
    assert FAKE_KEY not in str(sent["url"]) and cast(dict[str, str], sent["headers"])["X-Authorization"] == FAKE_KEY
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err


def test_parse_title_reads_progress_and_skips_broken_entries() -> None:
    raw = {"titleId": 123, "name": "X", "type": "Game", "devices": ["PC"], "achievement": {"progressPercentage": 47}}
    assert openxbl.parse_title(raw) == {
        "titleId": "123",
        "name": "X",
        "type": "Game",
        "devices": ["PC"],
        "progress": 47,
    }
    assert openxbl.parse_title({"name": "no id"}) is None


def test_minutes_stat_reads_minutes_played_and_skips_stats_without_a_value() -> None:
    def body(*stats: dict[str, object]) -> dict[str, object]:
        return {"content": {"groups": [], "statlistscollection": [{"arrangebyfield": "", "stats": list(stats)}]}}

    minutes = {"name": "MinutesPlayed", "type": "Integer", "value": "8471"}
    assert openxbl.minutes_stat(body({"name": "Wins", "value": "3"}, minutes)) == 8471
    assert openxbl.minutes_stat(body({"name": "MinutesPlayed", "type": "Integer"})) is None
    assert openxbl.minutes_stat({"content": {}}) is None and openxbl.minutes_stat(None) is None


def test_a_failed_minutes_request_is_false_not_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "XBOX_API_KEY", "")
    assert openxbl.get_minutes_played("123") is False  # no key: no request (the conftest blocks HTTP)
