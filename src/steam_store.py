"""Steam store `appdetails` scratch script: pull a game's description fields."""

import json
from pathlib import Path

from src.steam_api import StoreApp, get_game_details
from src.storage import write_json


def filter_out_descriptions(json_game_data: dict[str, StoreApp], game_id: str) -> dict[str, object]:
    """Keep only the description fields (detailed, short, about) that the store page has."""
    allowed_keys = {"detailed_description", "short_description", "about_the_game"}

    # unknown appids come back as {"success": false} with no "data"
    store_page = json_game_data[game_id].get("data", {})
    filtered_data = {key: store_page[key] for key in allowed_keys if key in store_page}

    return filtered_data


if __name__ == "__main__":
    ark_se_data = get_game_details("346110")
    if ark_se_data is None:
        raise SystemExit("appdetails request failed")
    filtered_descriptions = filter_out_descriptions(ark_se_data, "346110")

    write_json(Path("ark_se_data.json"), filtered_descriptions)

    print(json.dumps(filtered_descriptions, indent=2))
