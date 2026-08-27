import json

import requests

GAME_DATA = "https://store.steampowered.com/api/appdetails"


def get_game_data(game_id: int):
    query_params = {"appids": 346110}
    try:
        res = requests.get(GAME_DATA, query_params)
        if res.status_code == 200:
            data = res.json()
            return data
    except Exception as e:
        print(res.url)
        print(e)


def filter_out_descriptions(json_game_data, game_id):
    allowed_keys = {"detailed_description", "short_description", "about_the_game"}

    filtered_data = {
        key: json_game_data[game_id]["data"][key] for key in allowed_keys if key in json_game_data[game_id]["data"]
    }

    return filtered_data


if __name__ == "__main__":
    ark_se_data = get_game_data(346110)
    filtered_descriptions = filter_out_descriptions(ark_se_data, "346110")

    with open("ark_se_data.json", "w") as file:
        file.write(json.dumps(filtered_descriptions, indent=2))

    print(json.dumps(filtered_descriptions, indent=2))
