"""The harness itself: no test may reach the network or the player's data/."""

import sys
from pathlib import Path

import pytest
import requests

from src import config, steamspy

from .conftest import NetworkBlocked


def test_every_data_path_points_at_tmp(isolated_data: Path) -> None:
    real_data = config.ROOT / "data"
    for module_name, module in sys.modules.items():
        if not module_name.startswith("src"):
            continue
        for name, value in vars(module).items():
            if isinstance(value, Path) and name.isupper():
                assert not value.is_relative_to(real_data), f"{module_name}.{name} points at {value}"
                if value.is_relative_to(isolated_data.parent):
                    assert value.is_relative_to(isolated_data)


def test_http_is_blocked() -> None:
    with pytest.raises(NetworkBlocked):
        requests.get("https://steamspy.com/api.php", timeout=1)
    with pytest.raises(NetworkBlocked):
        steamspy.get_game_details(620)  # the client's RequestException handler can't swallow it
