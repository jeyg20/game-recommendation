"""Gemini chat assistant: reads the player's prompt, calls the tools in src/tools.py, answers from their results.

Function calling is handled by hand (automatic calling disabled) so every call and result can be logged.
"""

import time

from google import genai
from google.genai import types

from src import config, tools
from src.history import ToolCall, log_turn, new_session_id
from src.tools import TOOLS
from src.ui import status, style

MODEL = "gemini-3.1-flash-lite"
TOOL_LABELS = {  # what the status line says while a tool runs
    "get_taste_profile": "reading your taste profile",
    "recommend_games": "ranking games",
    "look_up_game": "looking the game up on Steam",
    "update_preferences": "saving your preferences",
    "refresh_library": "refreshing your Steam library",
    "update_background_corpus": "updating the tag corpus",
    "note_request": "noting that",
    "get_recommendation_history": "reading the session logs",
    "list_past_sessions": "listing past sessions",
    "start_new_session": "closing this session",
}
MAX_TOOL_ROUNDS = 8  # model -> tools -> model round trips per prompt, guards against a call loop

SYSTEM_PROMPT = """You are a game recommendation assistant for one Steam player. You have tools that read their
Steam library, build a taste profile from their playtime, rank games against it, and keep a log of past requests.

When to recommend:
- Call recommend_games only when the player asks for picks now: "recommend", "what should I play", "go ahead",
  "that's it", or a single message that is a complete request ("something like Portal 2").
- When the player is still describing what they want (listing games they liked, moods, things to avoid, or saying
  they'll add more), call note_request to record it, confirm in one short line what you noted, and ask whether there
  is more or they want picks now. Don't recommend yet.
- If you can't tell whether they're done, ask. Questions about their profile, a game or past sessions get answered
  with the matching tool, without recommending.

Recommending:
- Recommend only games returned by your tools; never invent titles.
- The player already sees every recommend_games result as a table (name, fit score, rating or hours, matched tags)
  right above your reply. Don't list the picks again. Instead, in a few short lines, say which 2-3 fit best and
  why, tied to what they asked, their most-played games and the player_notes; point out a backlog game if one fits.
- Steers ("more like X", "with co-op", "no horror") go to note_request or straight to recommend_games
  (like_games / boost_tags / avoid_tags). Noted steers are applied automatically; don't repeat them.
- Only call update_preferences when the player asks to remember or always apply something across sessions.
- Tags must be Steam user tag names. If a tool reports unknown tags, tell the player and offer the suggestions.
- The backlog is owned games never launched or only tried briefly (playtime_hours shows which); bring them up when
  they fit well.

Other tools:
- update_background_corpus is slow: ask before calling it. refresh_library takes a minute or two: say so first.
- Each session is logged separately. get_recommendation_history reads the current session by default; for earlier
  ones use list_past_sessions, then pass a session id (or "all"). When the player asks for a new session or a clean
  slate, call start_new_session.

Be concise. Use short lists.
"""


class Assistant:
    """The chat with Gemini. Each session has its own log file and its own model memory; new_session resets both."""

    def __init__(self) -> None:
        if not config.GEMINI_API:
            raise RuntimeError("GEMINI_API is not set in .env")
        self.tools = {tool.__name__: tool for tool in TOOLS}
        # kept on self: once the Client is garbage-collected it closes its HTTP client and the chat stops working
        self.client = genai.Client(api_key=config.GEMINI_API)
        self.new_session()

    @property
    def session(self) -> str:
        return tools.state["session"]

    def new_session(self) -> None:
        """Fresh log file and a fresh chat: nothing from the previous session reaches the model."""
        tools.state["session"] = new_session_id()
        tools.state["new_session_requested"] = False
        tools.state["draft"] = tools.empty_draft()
        self.chat = self.client.chats.create(
            model=MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=list(TOOLS),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

    def run_tool(self, call: types.FunctionCall) -> ToolCall:
        name = call.name or ""
        args = dict(call.args or {})
        label = TOOL_LABELS.get(name, name)
        started = time.monotonic()
        tool = self.tools.get(name)
        if tool is None:
            call: ToolCall = {"name": name, "args": args, "result": None, "error": f"unknown tool {name!r}"}
        else:
            try:
                with status.running(label):
                    call = {"name": name, "args": args, "result": tool(**args)}
            except Exception as e:  # the model gets the error and can recover or explain it
                call = {"name": name, "args": args, "result": None, "error": f"{type(e).__name__}: {e}"}

        elapsed = f"{time.monotonic() - started:.1f}s"
        if "error" in call:
            status.say(style(f"  ✗ {label}: {call['error']}", "red"))
        else:
            status.say(style(f"  ✓ {label}  {elapsed}", "grey"))
        return call

    def send(self, prompt: str) -> tuple[str, list[ToolCall]]:
        """Answer one prompt, running whatever tools the model asks for, and log the turn. (reply, tool calls)"""
        with status.running("thinking"):
            return self._send(prompt)

    def _send(self, prompt: str) -> tuple[str, list[ToolCall]]:
        calls: list[ToolCall] = []
        response = self.chat.send_message(prompt)

        for _ in range(MAX_TOOL_ROUNDS):
            if not response.function_calls:
                break
            parts: list[types.Part] = []
            for function_call in response.function_calls:
                call = self.run_tool(function_call)
                calls.append(call)
                payload = {"error": call["error"]} if "error" in call else {"result": call["result"]}
                parts.append(types.Part.from_function_response(name=call["name"], response=payload))
            status.update("thinking")
            response = self.chat.send_message(parts)

        reply = response.text or "(no reply: the model kept calling tools; try rephrasing)"
        log_turn(self.session, prompt, calls, reply)
        if tools.state["new_session_requested"]:
            self.new_session()
        return reply, calls
