"""Game recommendation assistant: chat with Gemini, which runs the library/profile/ranking tools for you.

Run from the repo root:
  python main.py              interactive assistant (needs GEMINI_API); each run is a new session
  python main.py --report     one-shot ranked report from the saved preferences, no LLM
  python main.py --sessions   list logged sessions and exit
  python main.py --open N     print session N in full and exit
"""

import argparse
import sys
from collections.abc import Callable
from typing import Any, cast

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent

from src import config, storage, tools
from src.ai_agent import Assistant
from src.history import Turn, list_sessions, load_session

HELP = """Ask for anything, e.g.:
  recommend me something new
  I want something like Hollow Knight and Celeste, but no pixel graphics
  always avoid horror games from now on
  refresh my steam library
  compare this with what you recommended in my last session
  start a new session
Enter sends. Pasted text stays one prompt, line breaks included; Alt+Enter adds a line break by hand.
Commands:
  /new         start a new session (fresh log, the model forgets this conversation)
  /history     this session's turns          /sessions   every logged session
  /open <n>    print session n in full       /help  /quit"""


def tool_summary(turn: Turn) -> list[str]:
    """One line per tool call; recommendation calls list their top picks and backlog."""
    lines: list[str] = []
    for call in turn["tool_calls"]:
        args = ", ".join(f"{key}={value!r}" for key, value in call["args"].items())
        lines.append(f"    {call['name']}({args})" + (f"  ERROR {call['error']}" if "error" in call else ""))
        result = call["result"]
        if not isinstance(result, dict):
            continue
        for key, label in (("recommendations", "top"), ("backlog", "backlog")):
            picks = cast(list[dict[str, Any]], result.get(key) or [])
            if picks:
                lines.append(f"      {label}: {'; '.join(f'{pick["name"]} ({pick["score"]})' for pick in picks[:5])}")
    return lines


def print_turns(turns: list[Turn], full: bool) -> None:
    for number, turn in enumerate(turns, start=1):
        print(f"\n[{number}] {turn['timestamp']}\n> {turn['prompt']}")
        print("\n".join(tool_summary(turn)))
        if full:
            print(f"\n{turn['reply']}")


def print_sessions() -> None:
    sessions = list_sessions()
    if not sessions:
        print(f"No sessions logged yet ({config.SESSIONS_DIR})")
        return
    for number, summary in enumerate(sessions, start=1):
        print(f"[{number}] {summary['session']}  {summary['turns']} turns  > {summary['first_prompt'][:70]}")
    print("\n/open <n> prints a session in full")


def open_session(number: int) -> None:
    sessions = list_sessions()
    if not 1 <= number <= len(sessions):
        print(f"No session {number} ({len(sessions)} logged)")
        return
    session = sessions[number - 1]["session"]
    print(f"=== Session {session} ===")
    print_turns(load_session(session), full=True)


def prompt_reader() -> Callable[[], str]:
    """Reads one prompt. In a terminal, prompt_toolkit's bracketed paste keeps a pasted block (newlines and all) in
    one prompt that Enter sends; plain input() would send every pasted line as its own prompt.
    Piped stdin (scripts, tests) falls back to input(): one line per prompt.
    """
    if not sys.stdin.isatty():
        return lambda: input("\n> ")

    bindings = KeyBindings()

    @bindings.add("escape", "enter")  # Alt+Enter
    def _(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    session: PromptSession[str] = PromptSession(key_bindings=bindings, prompt_continuation="  ")

    def read() -> str:
        print()
        return session.prompt("> ")

    return read


def chat() -> None:
    assistant = Assistant()
    read_prompt = prompt_reader()
    print(HELP)
    print(f"\nSession {assistant.session}")
    while True:
        try:
            prompt = read_prompt().strip()
        except EOFError, KeyboardInterrupt:
            print()
            return
        if not prompt:
            continue
        command, _, arg = prompt.partition(" ")
        if command in ("/quit", "/exit"):
            return
        if command == "/help":
            print(HELP)
        elif command == "/new":
            assistant.new_session()
            print(f"New session {assistant.session}")
        elif command == "/history":
            turns = load_session(assistant.session)
            if turns:
                print_turns(turns, full=False)
            else:
                print("Nothing in this session yet")
        elif command == "/sessions":
            print_sessions()
        elif command == "/open" and arg.isdigit():
            open_session(int(arg))
        else:
            session = assistant.session
            try:
                print(f"\n{assistant.send(prompt)}")
            except KeyboardInterrupt:
                print("\n(interrupted)")
            if assistant.session != session:
                print(f"\nNew session {assistant.session}")


def report() -> None:
    """The ranking without the assistant: saved preferences applied, results printed and saved."""
    result = tools.recommend_games(limit=15)
    print(f"\nSteered top tags: {', '.join(cast(dict[str, float], result['steered_top_tags']))}")
    for title, key in (
        ("Recommended for you", "recommendations"),
        ("Already in your library, never played", "backlog"),
    ):
        print(f"\n=== {title} ===")
        for rank, pick in enumerate(cast(list[dict[str, Any]], result.get(key, [])), start=1):
            print(f"{rank:>2}. {pick['name']}  [{pick['score']:.2f}]")
            print(f"    {', '.join(pick['matched_tags'])}")
    storage.write_json(config.RECOMMENDATIONS_PATH, result)
    print(f"\nFull results saved to {config.RECOMMENDATIONS_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="print a ranked report without the assistant")
    mode.add_argument("--sessions", action="store_true", help="list logged sessions")
    mode.add_argument("--open", type=int, metavar="N", help="print logged session N in full")
    args = parser.parse_args()

    if args.report:
        report()
    elif args.sessions:
        print_sessions()
    elif args.open is not None:
        open_session(args.open)
    else:
        chat()
