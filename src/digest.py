"""Weekly digest: the report's ranking as a short Markdown file that leads with what's new since the last digest.

The diff and the rendering are pure functions of the result and the previous digest's state; `load_state` and
`save_state` are the only I/O. Tables come from export.py, so a digest looks like an `--export` file.
"""

from collections.abc import Mapping
from datetime import date
from typing import TypedDict

from src import config, export, storage

TITLE = "Gamer Guide digest"
BACKLOG_PICKS = 5
# picks diffed against the last digest: (result key, heading, extra column header, extra field)
DIFFED = (
    ("recommendations", "New for you", "Liked", "positive_ratio"),
    ("wildcards", "New wildcards: a step outside your usual tags", "Liked", "positive_ratio"),
)

type Pick = Mapping[str, object]


class DigestState(TypedDict):
    date: str  # ISO day of the last digest
    appids: list[int]  # every recommendation and wildcard it listed, new or not


def picks(result: Mapping[str, object], key: str) -> list[Pick]:
    found = result.get(key)
    return [pick for pick in found if isinstance(pick, Mapping)] if isinstance(found, list) else []


def listed(result: Mapping[str, object]) -> list[int]:
    """Appids of every diffed pick in the result, in order: what the next digest compares against."""
    appids = (pick.get("appid") for key, *_ in DIFFED for pick in picks(result, key))
    return list(dict.fromkeys(appid for appid in appids if isinstance(appid, int)))


def new_picks(result: Mapping[str, object], key: str, seen: set[int]) -> list[Pick]:
    return [pick for pick in picks(result, key) if pick.get("appid") not in seen]


def to_markdown(result: Mapping[str, object], previous: DigestState | None, day: date) -> str:
    """New picks first, then the best backlog games to start, then when the previous digest was."""
    seen = set(previous["appids"]) if previous else set()
    lines = [f"# {TITLE}, {day.isoformat()}", ""]
    if previous is None:
        lines += ["First digest: every pick is new.", ""]
    applied = export.steers(result)
    if applied:
        lines += [f"- **{label}:** {export.md_escape(values)}" for label, values in applied] + [""]

    new = [
        export.Section(title, found, *extra) for key, title, *extra in DIFFED if (found := new_picks(result, key, seen))
    ]
    if previous is not None and not new:
        lines += [f"Nothing new since {previous['date']}.", ""]
    for section in new:
        lines += export.md_table(section)
    still = [str(pick.get("name", "?")) for pick in picks(result, "recommendations") if pick.get("appid") in seen]
    if still:
        lines += [f"Still recommended: {export.md_escape(', '.join(still))}", ""]

    backlog = picks(result, "backlog")[:BACKLOG_PICKS]
    if backlog:
        lines += export.md_table(export.Section("Start from your backlog", backlog, "Hours", "playtime_hours"))
    lines.append(f"Previous digest: {previous['date'] if previous else 'none'}")
    return "\n".join(lines) + "\n"


def state(result: Mapping[str, object], day: date) -> DigestState:
    return {"date": day.isoformat(), "appids": listed(result)}


def parse_state(data: object) -> DigestState | None:
    """The saved state, or None when it's missing a field (a damaged file then counts as a first digest)."""
    if not isinstance(data, Mapping):
        return None
    day, appids = data.get("date"), data.get("appids")
    if not isinstance(day, str) or not isinstance(appids, list):
        return None
    return {"date": day, "appids": [appid for appid in appids if isinstance(appid, int)]}


def load_state() -> DigestState | None:
    if not config.DIGEST_STATE_PATH.exists():
        return None
    return parse_state(storage.read_json(config.DIGEST_STATE_PATH))


def save_state(result: Mapping[str, object], day: date) -> None:
    storage.write_json(config.DIGEST_STATE_PATH, state(result, day))
