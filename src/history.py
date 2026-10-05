"""Append-only log of every assistant turn: the prompt, each tool call with its args and full result, the reply.

One JSON record per line in data/logs/history.jsonl, so past recommendations can be compared across prompts.
"""

from datetime import datetime
from typing import NotRequired, TypedDict, cast

from src.config import HISTORY_PATH
from src.storage import append_jsonl, read_jsonl


class ToolCall(TypedDict):
    name: str
    args: dict[str, object]
    result: object
    error: NotRequired[str]


class Turn(TypedDict):
    timestamp: str  # ISO, local time
    session: str  # one id per `python main.py` run
    prompt: str
    tool_calls: list[ToolCall]
    reply: str


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def log_turn(session: str, prompt: str, tool_calls: list[ToolCall], reply: str) -> None:
    turn: Turn = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session": session,
        "prompt": prompt,
        "tool_calls": tool_calls,
        "reply": reply,
    }
    append_jsonl(HISTORY_PATH, turn)


def load_turns() -> list[Turn]:
    """Oldest first."""
    if not HISTORY_PATH.is_file():
        return []
    return [cast(Turn, record) for record in read_jsonl(HISTORY_PATH)]
