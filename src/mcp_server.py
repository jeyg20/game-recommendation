"""MCP server: the same tools the Gemini assistant uses, served over stdio so Claude Code / Claude Desktop does the
reasoning instead of the Gemini API. The CLI keeps working; this is a second frontend.

A server process can outlive a conversation (Claude Desktop shares one across chats, Claude Code keeps it across
/clear), so `start_new_session` here resets the session right away and the instructions ask for it at the start of
each conversation. Each session gets its own log under data/logs/sessions/; each tool call is logged as a turn, since
the server never sees the player's prompts or Claude's replies.

stdout carries the protocol. The SDK's stdio transport moves fd 1 onto stderr and talks over a private copy, so the
status messages the library steps print end up in the client's stderr log instead of breaking the stream.
"""

import copy
import functools
import threading
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from src import tools
from src.history import ToolCall, log_turn, new_session_id

INSTRUCTIONS = """Game recommendation tools for one Steam player. They read the player's Steam library, build a taste
profile from their playtime, rank games they don't own against it, and keep a log of past requests.

Start of a conversation: call start_new_session once before any other tool, and again when the player asks for a
new session or a clean slate. The server can outlive a chat, so otherwise an earlier chat's noted request carries over.

When to recommend:
- Call recommend_games only when the player asks for picks now ("recommend", "what should I play", "go ahead",
  "that's it") or sends a single message that is a complete request ("something like Portal 2").
- While the player is still describing what they want (games they liked, moods, things to avoid), call note_request
  to record it, confirm in one short line what you noted, and ask whether there is more or they want picks now.
- Questions about their profile, a game or past sessions get answered with the matching tool, without recommending.

Recommending:
- Recommend only games returned by the tools; never invent titles.
- Show the picks as a Markdown table: rank, name, fit score, liked (positive_ratio as a percentage) and matched tags.
  Show backlog picks as a second table with playtime_hours instead of liked. Then, in a few short lines, say which
  2-3 fit best and why, tied to what they asked, their most-played games and the player_notes.
- Noted steers are applied automatically by recommend_games; don't pass them again. like_games = "more like X";
  boost_tags = must-haves ("a shooter", "has to be co-op"): every pick will carry them, so use them only for what the
  player actually requires and keep softer wishes as notes; avoid_tags = "no horror". If the result says the
  requirement was relaxed or a boost tag wasn't enforced, tell the player.
- Only call update_preferences when the player asks to remember or always apply something across sessions.
- Tags must be Steam user tag names. If a tool reports unknown tags, tell the player and offer the suggestions.
- The backlog is owned games never launched or only tried briefly; bring them up when they fit well.

Other tools:
- update_background_corpus is slow (minutes, or over an hour with new listings): ask before calling it.
  refresh_library takes a minute or two: say so first.
- get_recommendation_history reads this session by default; for earlier ones use list_past_sessions, then pass a
  session id (or "all").

Be concise. Use short lists.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
READS_STEAM = ToolAnnotations(read_only_hint=True, open_world_hint=True)
ANNOTATIONS: dict[str, ToolAnnotations] = {  # the tools.TOOLS served; start_new_session is replaced below
    "get_taste_profile": READ_ONLY,
    "recommend_games": READS_STEAM,  # writes only caches
    "look_up_game": READS_STEAM,
    "update_preferences": ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    "refresh_library": ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    ),
    "update_background_corpus": ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
    ),
    "note_request": ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    "get_recommendation_history": READ_ONLY,
    "list_past_sessions": READ_ONLY,
}

# Sync tools run on worker threads, so Claude's parallel calls would race on tools.state (ensure_loaded checks
# then sets, refresh_library swaps the library, note_request edits the draft). One player, one lock.
tool_lock = threading.Lock()


def logged(tool: Callable[..., dict[str, object]]) -> Callable[..., dict[str, object]]:
    """Run `tool` under the lock and log the call as a turn. functools.wraps keeps the signature and docstring the
    SDK turns into the tool's schema and description."""

    @functools.wraps(tool)
    def run(**kwargs: object) -> dict[str, object]:
        call: ToolCall = {"name": tool.__name__, "args": dict(kwargs), "result": None}
        with tool_lock:
            try:
                result = copy.deepcopy(tool(**kwargs))  # results can hold the live draft: snapshot it now
                call["result"] = result
                return result
            except Exception as e:
                call["error"] = f"{type(e).__name__}: {e}"
                raise
            finally:
                log_turn(tools.state["session"], f"[mcp] {tool.__name__}", [call], "")

    return run


def start_new_session() -> dict[str, object]:
    """Start a fresh session right away: a new log and an empty request draft, so nothing noted earlier is applied.

    Call once at the start of every conversation, and when the player asks for a new session or a clean slate.
    Saved preferences still apply; use update_preferences to change them.
    """
    with tool_lock:
        closed = tools.state["session"]
        tools.state["session"] = new_session_id()
        tools.state["draft"] = tools.empty_draft()
        return {"closed_session": closed, "session": tools.state["session"]}


def build_server() -> MCPServer:
    server = MCPServer(name="gamer-guide", instructions=INSTRUCTIONS)
    for tool in tools.TOOLS:
        if tool.__name__ in ANNOTATIONS:
            server.add_tool(logged(tool), annotations=ANNOTATIONS[tool.__name__])
    # not logged: it would become every session's first turn
    server.add_tool(start_new_session, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
    return server


def main() -> None:
    tools.state["session"] = new_session_id()
    build_server().run()


if __name__ == "__main__":
    main()
