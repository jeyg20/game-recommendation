"""Session logs: one JSONL file per chat session under data/logs/sessions/, one record per turn.

A turn holds the prompt, each tool call with its args and full result, and the reply, so past recommendations and
the reasons for them can be read back and compared across sessions. A session starts with every `python main.py`
run and again whenever the player asks for a new one.
"""

from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from src.config import SESSIONS_DIR
from src.storage import append_jsonl, read_jsonl


class ToolCall(TypedDict):
    name: str
    args: dict[str, object]
    result: object
    error: NotRequired[str]


class Turn(TypedDict):
    timestamp: str  # ISO, local time
    prompt: str
    tool_calls: list[ToolCall]
    reply: str


class SessionSummary(TypedDict):
    session: str
    started: str
    turns: int
    first_prompt: str


def new_session_id() -> str:
    """Sortable and readable: the session's start time, down to the second."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def session_path(session: str) -> Path:
    return SESSIONS_DIR / f"{session}.jsonl"


def log_turn(session: str, prompt: str, tool_calls: list[ToolCall], reply: str) -> None:
    turn: Turn = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "prompt": prompt,
        "tool_calls": tool_calls,
        "reply": reply,
    }
    append_jsonl(session_path(session), turn)


def load_session(session: str) -> list[Turn]:
    """The session's turns, oldest first. Empty for a session with no turns yet (no file is written until then)."""
    path = session_path(session)
    if not path.is_file():
        return []
    return [cast(Turn, record) for record in read_jsonl(path)]


def list_sessions() -> list[SessionSummary]:
    """Every logged session, oldest first."""
    if not SESSIONS_DIR.is_dir():
        return []
    summaries: list[SessionSummary] = []
    for path in sorted(SESSIONS_DIR.glob("*.jsonl")):
        turns = load_session(path.stem)
        if not turns:
            continue
        summaries.append(
            {
                "session": path.stem,
                "started": turns[0]["timestamp"],
                "turns": len(turns),
                "first_prompt": turns[0]["prompt"],
            }
        )
    return summaries
