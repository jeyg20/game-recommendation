"""Game recommendation assistant: chat with Gemini, which runs the library/profile/ranking tools for you.

gamer-guide              interactive assistant (needs GEMINI_API); each run is a new session
gamer-guide --report     one-shot ranked report from the saved preferences, no LLM
gamer-guide --sessions   list logged sessions and exit
gamer-guide --open N     print session N in full and exit
"""

import argparse
import sys
from collections.abc import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent

from src import config, storage, tools, ui
from src.ai_agent import TOOL_LABELS, Assistant
from src.history import ToolCall, Turn, list_sessions, load_session
from src.tag_profile import load_df_table
from src.ui import style

COMMANDS = {
    "/new": "start a new session (fresh log, the model forgets this conversation)",
    "/history": "this session's turns",
    "/sessions": "every logged session",
    "/open": "print session <n> in full",
    "/help": "show this help",
    "/quit": "leave",
}
EXAMPLES = [
    "recommend me something new",
    "I want something like Hollow Knight and Celeste, but no pixel graphics",
    "always avoid horror games from now on",
    "refresh my steam library",
    "compare this with what you recommended in my last session",
]


def help_text() -> str:
    lines = [style("Ask for anything, e.g.", "bold")]
    lines += [f"  {style('›', 'cyan')} {example}" for example in EXAMPLES]
    lines += ["", style("Commands", "bold")]
    lines += [f"  {style(command.ljust(10), 'cyan')} {description}" for command, description in COMMANDS.items()]
    lines += ["", style("Enter sends · pasted text stays one prompt · Alt+Enter new line · Tab completes tags", "grey")]
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------
def tool_tables(calls: Iterable[ToolCall]) -> str:
    """Tables for every recommendation made in a turn, straight from the logged results."""
    blocks = [
        ui.recommendation_tables(call["result"])
        for call in calls
        if call["name"] == "recommend_games" and isinstance(call["result"], dict)
    ]
    return "\n".join(block for block in blocks if block)


def print_reply(reply: str, calls: list[ToolCall]) -> None:
    tables = tool_tables(calls)
    if tables:
        print(tables)
    print(f"\n{ui.render_markdown(reply)}")


def print_turn(number: int, turn: Turn, full: bool) -> None:
    time = turn["timestamp"].partition("T")[2]
    print(f"\n{style(f'[{number}]', 'cyan')} {style(time, 'grey')}  {style(turn['prompt'], 'bold')}")
    for call in turn["tool_calls"]:
        label = TOOL_LABELS.get(call["name"], call["name"])
        print(style(f"  ✗ {label}: {call['error']}", "red") if "error" in call else style(f"  ✓ {label}", "grey"))
    if full:
        print_reply(turn["reply"], turn["tool_calls"])
        return
    for call in turn["tool_calls"]:
        result = call["result"]
        if call["name"] == "recommend_games" and isinstance(result, dict):
            picks = result.get("recommendations") or []
            names = ", ".join(str(pick["name"]) for pick in picks[:5])
            print(f"    {style('top', 'cyan')} {ui.truncate(names, ui.width() - 10)}")


def print_sessions() -> None:
    sessions = list_sessions()
    if not sessions:
        print(f"No sessions logged yet ({config.SESSIONS_DIR})")
        return
    print(style(f"  {'#':>3}  {'Started':<16}  {'Turns':>5}  First prompt", "grey"))
    for number, summary in enumerate(sessions, start=1):
        started = summary["started"].replace("T", " ")[:16]
        prompt = ui.truncate(summary["first_prompt"].replace("\n", " "), ui.width() - 34)
        print(f"  {style(f'{number:>3}', 'cyan')}  {started:<16}  {summary['turns']:>5}  {prompt}")
    print(style("\n/open <n> prints a session in full", "grey"))


def open_session(number: int) -> None:
    sessions = list_sessions()
    if not 1 <= number <= len(sessions):
        print(f"No session {number} ({len(sessions)} logged)")
        return
    session = sessions[number - 1]["session"]
    print(style(f"━━ Session {session} ━━", "bold", "cyan"))
    for turn_number, turn in enumerate(load_session(session), start=1):
        print_turn(turn_number, turn, full=True)


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------
class PromptCompleter(Completer):
    """Slash commands at the start of the line; Steam tag names (which can be several words) anywhere else."""

    def __init__(self) -> None:
        self.tags: list[str] | None = None

    def tag_names(self) -> list[str]:
        if self.tags is None:
            # Only the df table: ensure_loaded() can reach asyncio.run, which fails inside prompt_toolkit's event loop
            try:
                if tools.state["df_table"] is None:
                    tools.state["df_table"] = load_df_table()
                self.tags = sorted(tools.state["df_table"]["df"])
            except RuntimeError:  # no corpus yet: complete commands only
                self.tags = []
        return self.tags

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for command, description in COMMANDS.items():
                if command.startswith(text):
                    yield Completion(command, -len(text), display_meta=description)
            return

        words = text.split(" ")
        seen: set[str] = set()
        for count in range(
            min(len(words), 4), 0, -1
        ):  # longest fragment first: "open world su" -> "Open World Survival…"
            fragment = " ".join(words[-count:]).lstrip(",;(\"'")
            if len(fragment) < 2:
                continue
            for tag in self.tag_names():
                if tag.lower().startswith(fragment.lower()) and tag not in seen:
                    seen.add(tag)
                    yield Completion(tag, -len(fragment), display_meta="Steam tag")


def toolbar() -> str:
    """Bottom bar: session id and what the current request draft holds."""
    draft = tools.state["draft"]
    parts = [f" session {tools.state['session']}"]
    for key, label in (("like_games", "like"), ("boost_tags", "must have"), ("avoid_tags", "avoid"), ("notes", "note")):
        if draft[key]:
            parts.append(f"{label}: {', '.join(draft[key])}")
    if len(parts) == 1:
        parts.append("nothing noted yet")
    return ui.truncate("  ·  ".join(parts), ui.width() - 1)


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

    session: PromptSession[str] = PromptSession(
        key_bindings=bindings,
        prompt_continuation="  ",
        completer=PromptCompleter(),
        bottom_toolbar=toolbar,
    )

    def read() -> str:
        print()
        return session.prompt([("ansicyan bold", "› ")])

    return read


# ---------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------
def chat() -> None:
    assistant = Assistant()
    read_prompt = prompt_reader()
    print(style("Gamer Guide", "bold", "cyan") + style(f"  session {assistant.session}", "grey"))
    print(help_text())
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
            print(help_text())
        elif command == "/new":
            assistant.new_session()
            print(style(f"New session {assistant.session}", "cyan"))
        elif command == "/history":
            turns = load_session(assistant.session)
            if not turns:
                print("Nothing in this session yet")
            for number, turn in enumerate(turns, start=1):
                print_turn(number, turn, full=False)
        elif command == "/sessions":
            print_sessions()
        elif command == "/open" and arg.isdigit():
            open_session(int(arg))
        else:
            session = assistant.session
            try:
                reply, calls = assistant.send(prompt)
                print_reply(reply, calls)
            except KeyboardInterrupt:
                print(style("\n(interrupted)", "grey"))
            if assistant.session != session:
                print(style(f"\nNew session {assistant.session}", "cyan"))


def report() -> None:
    """The ranking without the assistant: saved preferences applied, results printed and saved."""
    with ui.status.running("ranking games"):
        result = tools.recommend_games(limit=15)
    print(ui.recommendation_tables(result))
    storage.write_json(config.RECOMMENDATIONS_PATH, result)
    print(style(f"\nFull results saved to {config.RECOMMENDATIONS_PATH}", "grey"))


def main() -> None:
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
