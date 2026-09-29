"""JSON / JSONL file I/O. Every read and write in the project goes through here (UTF-8, parents created)."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast


def read_json(path: Path) -> object:
    """Parse a JSON file. Returns `object`: callers narrow or `cast` it to the shape they wrote."""
    json_string = path.read_text(encoding="utf-8")
    return json.loads(json_string)


def write_json(path: Path, data: object) -> None:
    """Write `data` as indented JSON, creating parent folders. `data` must be JSON-serializable.

    Written to a temp file and swapped in, so a crash mid-write never leaves a truncated file behind
    (load_or_build would otherwise treat it as a valid cache and fail on every run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_jsonl(path: Path) -> list[object]:
    """One record per line; blank and partial/corrupt lines are skipped."""
    records: list[object] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_jsonl(path: Path, record: object) -> None:
    """Append one JSON record as a new line, creating parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def load_or_build[T](path: Path, build: Callable[[], T]) -> T:
    """Read the cached file if it exists, otherwise build the data and cache it.

    The cached file is trusted to have the shape `build` returns. JSON turns int dict keys
    into str, so a dict keyed by int comes back keyed by str on a cache hit.
    """
    if path.is_file():
        return cast(T, read_json(path))
    data = build()
    write_json(path, data)
    return data


async def load_or_build_async[T](path: Path, build: Callable[[], Awaitable[T]]) -> T:
    """`load_or_build` for a coroutine builder (e.g. HLTB lookups)."""
    if path.is_file():
        return cast(T, read_json(path))
    data = await build()
    write_json(path, data)
    return data
