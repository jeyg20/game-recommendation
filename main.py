"""Game recommendation assistant: chat with Gemini, which runs the library/profile/ranking tools for you.

Run from the repo root:
  python main.py            interactive assistant (needs GEMINI_API)
  python main.py --report   one-shot ranked report from the saved preferences, no LLM
  python main.py --history  print the log of past turns and exit
"""

import argparse
from typing import Any, cast

from src import config, storage, tools
from src.ai_agent import Assistant
from src.history import Turn, load_turns, new_session_id

HELP = """Ask for anything, e.g.:
  recommend me something new
  I want something like Hollow Knight and Celeste, but no pixel graphics
  always avoid horror games from now on
  refresh my steam library
  what did you recommend me last time, and how does it compare to now?
Commands: /history [n]   /show <n>   /help   /quit"""


def tool_summary(turn: Turn) -> list[str]:
    """One line per tool call; recommendation calls list their top picks."""
    lines: list[str] = []
    for call in turn["tool_calls"]:
        args = ", ".join(f"{key}={value!r}" for key, value in call["args"].items())
        lines.append(f"    {call['name']}({args})" + (f"  ERROR {call['error']}" if "error" in call else ""))
        result = call["result"]
        if isinstance(result, dict) and isinstance(result.get("recommendations"), list):
            picks = [f"{pick['name']} ({pick['score']})" for pick in result["recommendations"][:5]]
            lines.append(f"      top: {'; '.join(picks)}")
    return lines


def print_history(count: int) -> None:
    turns = load_turns()
    if not turns:
        print(f"No history yet ({config.HISTORY_PATH})")
        return
    start = max(len(turns) - count, 0)
    for index in range(start, len(turns)):
        turn = turns[index]
        print(f"\n[{index + 1}] {turn['timestamp']}  {turn['prompt']}")
        print("\n".join(tool_summary(turn)))
    print("\n/show <n> prints a turn's full reply")


def show_turn(number: int) -> None:
    turns = load_turns()
    if not 1 <= number <= len(turns):
        print(f"No turn {number} (log has {len(turns)})")
        return
    turn = turns[number - 1]
    print(f"\n[{number}] {turn['timestamp']}  session {turn['session']}\n> {turn['prompt']}")
    print("\n".join(tool_summary(turn)))
    print(f"\n{turn['reply']}")


def chat() -> None:
    assistant = Assistant(new_session_id())
    print(HELP)
    while True:
        try:
            prompt = input("\n> ").strip()
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
        elif command == "/history":
            print_history(int(arg) if arg.isdigit() else 10)
        elif command == "/show" and arg.isdigit():
            show_turn(int(arg))
        else:
            try:
                print(f"\n{assistant.send(prompt)}")
            except KeyboardInterrupt:
                print("\n(interrupted)")


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
    mode.add_argument("--history", type=int, nargs="?", const=20, metavar="N", help="print the last N logged turns")
    args = parser.parse_args()

    if args.report:
        report()
    elif args.history is not None:
        print_history(args.history)
    else:
        chat()
