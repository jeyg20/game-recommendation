"""Shared fixtures. Every test runs offline and against a temp copy of the data paths, never the player's data/."""

import math
import socket
import sys
from pathlib import Path

import pytest
import requests

# loaded up front so isolated_data finds every module that copied a config path by name
import src.cli
import src.mcp_server  # noqa: F401
from src import config, tools
from src.tag_profile import DfTable


class NetworkBlocked(RuntimeError):
    """Raised on any HTTP or socket call from a test. Not a RequestException, so the clients can't swallow it."""


def _blocked(*args: object, **kwargs: object) -> None:
    raise NetworkBlocked("tests are offline: monkeypatch the fetcher instead")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def data_paths() -> dict[str, Path]:
    """Every config constant that points into the repo's data/ folder."""
    data_dir = config.ROOT / "data"
    return {
        name: value
        for name, value in vars(config).items()
        if isinstance(value, Path) and value.is_relative_to(data_dir)
    }


@pytest.fixture(autouse=True)
def isolated_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every data path at tmp_path, in config and in each src module that imported it by name.

    New paths added to config.py are picked up automatically, as long as they live under data/.
    """
    data_dir = config.ROOT / "data"
    tmp_data = tmp_path / "data"
    for name, real in data_paths().items():
        fake = tmp_data / real.relative_to(data_dir)
        monkeypatch.setattr(config, name, fake)
        for module_name, module in list(sys.modules.items()):
            if module_name.startswith("src.") and getattr(module, name, None) == real:
                monkeypatch.setattr(module, name, fake)
    return tmp_data


@pytest.fixture(autouse=True)
def fresh_state() -> None:
    """Reset the tools' shared state and reference cache, so no test sees another's library or draft."""
    tools.state.update(
        {
            "player_games": None,
            "games": None,
            "df_table": None,
            "profile": None,
            "session": "",
            "new_session_requested": False,
            "draft": tools.empty_draft(),
        }
    )
    tools.reference_cache.clear()


@pytest.fixture
def df_table() -> DfTable:
    """A tiny corpus of 100 games: common tags get a low idf, rare ones a high idf."""
    n = 100
    df = {
        "Action": 60,
        "Indie": 80,
        "RPG": 30,
        "Roguelike": 10,
        "Shooter": 20,
        "Horror": 15,
        "Co-op": 25,
        "Puzzle": 12,
    }
    # the same smoothed idf as tag_profile.build_df_table
    return {"n": n, "df": df, "idf": {tag: math.log((1 + n) / (1 + count)) + 1 for tag, count in df.items()}}
