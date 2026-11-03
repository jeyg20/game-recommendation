"""OpenXBL (xbl.io) client: the player's Xbox title history and minutes played. A third-party wrapper over Xbox Live,
150 requests/hour.

The API key goes in a header, never in a URL or a log line. Failures print the error type and HTTP status only.
"""

from typing import Literal, TypedDict, cast

import requests

from src import config
from src.ui import status, style

OPENXBL_URL = "https://xbl.io/api/v2"


class XboxTitle(TypedDict):
    """One title from `/titles`, parsed down to what the recommender uses. Hours come per title (get_minutes_played)."""

    titleId: str
    name: str
    type: str  # "Game" for games; apps come back too
    devices: list[str]  # e.g. ["Win32"], ["PC", "XboxSeries"]
    progress: float  # achievement.progressPercentage, 0-100 (totalAchievements can be 0 while progress exists)


def parse_title(raw: object) -> XboxTitle | None:
    """An XboxTitle from one raw `/titles` entry, or None when it lacks an id or a name."""
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, object], raw)
    title_id, name = entry.get("titleId"), entry.get("name")
    if not title_id or not isinstance(name, str) or not name:
        return None
    achievement = entry.get("achievement")
    progress = cast(dict[str, object], achievement).get("progressPercentage") if isinstance(achievement, dict) else 0
    devices = entry.get("devices")
    return {
        "titleId": str(title_id),
        "name": name,
        "type": str(entry.get("type") or ""),
        "devices": [str(device) for device in cast(list[object], devices)] if isinstance(devices, list) else [],
        "progress": float(progress) if isinstance(progress, int | float) else 0.0,
    }


def get(path: str) -> object | None:
    """The JSON body of one OpenXBL GET, or None with no key or on any HTTP/network/JSON error (said, without the key)."""
    if not config.XBOX_API_KEY:
        return None
    try:
        res = requests.get(
            f"{OPENXBL_URL}/{path}",
            headers={"X-Authorization": config.XBOX_API_KEY, "Accept": "application/json"},
            timeout=20,
        )
        res.raise_for_status()
        return res.json()
    except requests.RequestException as e:
        code = e.response.status_code if e.response is not None else "no response"
        status.say(style(f"  ! OpenXBL request failed: {type(e).__name__} ({code})", "yellow"))
        return None


def get_titles() -> list[XboxTitle] | None:
    """Every title in the player's Xbox history, or None with no key or on any HTTP/network/JSON error."""
    data = get("titles")
    if data is None:
        return None
    content = data.get("content") if isinstance(data, dict) else None
    titles = content.get("titles") if isinstance(content, dict) else None
    if not isinstance(titles, list):
        status.say(style("  ! OpenXBL answered without a title list", "yellow"))
        return None
    return [title for raw in titles if (title := parse_title(raw)) is not None]


def minutes_stat(data: object) -> int | None:
    """The MinutesPlayed value in an `/achievements/stats/{titleId}` body, or None when no stat carries one."""
    content = data.get("content") if isinstance(data, dict) else None
    collections = content.get("statlistscollection") if isinstance(content, dict) else None
    for collection in collections if isinstance(collections, list) else []:
        stats = collection.get("stats") if isinstance(collection, dict) else None
        for stat in stats if isinstance(stats, list) else []:
            entry = cast(dict[str, object], stat) if isinstance(stat, dict) else {}
            value = entry.get("value")
            if entry.get("name") == "MinutesPlayed" and isinstance(value, str | int) and str(value).isdigit():
                return int(value)
    return None


def get_minutes_played(title_id: str) -> int | None | Literal[False]:
    """Minutes the player spent in one title (one request), None when OpenXBL has no MinutesPlayed for it, False when
    the request failed (no key, HTTP 429 over the hourly limit, network): a failure is not "no minutes"."""
    data = get(f"achievements/stats/{title_id}")
    return False if data is None else minutes_stat(data)
