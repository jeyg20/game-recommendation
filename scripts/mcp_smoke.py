#!/usr/bin/env -S uv run python
"""Call the MCP server's tools the way a client does, to prove a tool works over the protocol and not just in Python.

    scripts/mcp_smoke.py                                    # initialize and list the tools
    scripts/mcp_smoke.py get_taste_profile '{"top_n": 5}' look_up_game '{"name": "Hades"}'

Spawns `uv run gamer-guide-mcp`, speaks raw JSON-RPC over its stdio and prints each tool's result. Exits 1 when a
tool returns an error, or when any line the server writes to stdout isn't JSON-RPC (a stray print breaks real
clients). Server stderr (status messages) passes through. The session logs of this run's server are deleted afterwards
(only its own session ids: a real client's log created meanwhile is left alone).
"""

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports when run as a plain script

from src.config import SESSIONS_DIR

PROTOCOL_VERSION = "2025-11-25"
TIMEOUT_SECONDS = 600  # recommend_games can fetch new tag pools at 1 request/second

type Message = dict[str, object]


class SmokeError(Exception):
    pass


class Server:
    """The server process, with every stdout line checked as JSON-RPC on a reader thread."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["uv", "run", "--project", str(ROOT), "--quiet", "gamer-guide-mcp"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.messages: queue.Queue[Message | SmokeError | None] = queue.Queue()
        self.next_id = 1
        threading.Thread(target=self.read_stdout, daemon=True).start()

    def read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.messages.put(SmokeError(f"stdout line is not JSON: {line.rstrip()!r}"))
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                self.messages.put(SmokeError(f"stdout line is not JSON-RPC: {line.rstrip()!r}"))
                continue
            self.messages.put(cast(Message, message))
        self.messages.put(None)  # stdout closed

    def send(self, message: Message) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: Message) -> Message:
        """Send a request and wait for its response, skipping the server's notifications."""
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            try:
                message = self.messages.get(timeout=TIMEOUT_SECONDS)
            except queue.Empty:
                raise SmokeError(f"{method}: no response in {TIMEOUT_SECONDS}s") from None
            if isinstance(message, SmokeError):
                raise message
            if message is None:
                raise SmokeError(f"{method}: server exited (code {self.process.wait()})")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise SmokeError(f"{method}: JSON-RPC error {message['error']}")
            return cast(Message, message["result"])

    def close(self) -> None:
        """Close stdin, let the server exit, and check what it wrote on the way out."""
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        while True:
            try:
                message = self.messages.get(timeout=5)
            except queue.Empty:
                return
            if message is None:
                return
            if isinstance(message, SmokeError):
                raise message


def parse_calls(argv: list[str]) -> list[tuple[str, Message]]:
    """`name '{json args}'` pairs; the args may be left off for the last tool."""
    calls: list[tuple[str, Message]] = []
    i = 0
    while i < len(argv):
        name = argv[i]
        args: object = {}
        if i + 1 < len(argv) and argv[i + 1].lstrip().startswith("{"):
            try:
                args = json.loads(argv[i + 1])
            except json.JSONDecodeError as e:
                raise SmokeError(f"{name}: arguments aren't valid JSON: {e}") from e
            i += 1
        if not isinstance(args, dict):
            raise SmokeError(f"{name}: arguments must be a JSON object")
        calls.append((name, cast(Message, args)))
        i += 1
    return calls


def tool_output(result: Message) -> object:
    """The structured result when the server sends one, otherwise the text content."""
    if "structuredContent" in result:
        return result["structuredContent"]
    content = cast(list[dict[str, object]], result.get("content", []))
    texts = [str(block.get("text", "")) for block in content if block.get("type") == "text"]
    try:
        return json.loads("".join(texts))
    except json.JSONDecodeError:
        return "".join(texts)


def run(calls: list[tuple[str, Message]], sessions: set[str]) -> bool:
    """True when every call succeeded. Adds every session id the server used to `sessions`, for cleanup."""
    server = Server()
    ok = True
    try:
        init = server.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp_smoke", "version": "1"},
            },
        )
        server_info = cast(dict[str, object], init.get("serverInfo", {}))
        print(f"initialized: {server_info.get('name')} (protocol {init.get('protocolVersion')})")
        server.send({"method": "notifications/initialized"})

        listed = cast(list[dict[str, object]], server.request("tools/list", {})["tools"])
        names = [str(tool["name"]) for tool in listed]
        print(f"tools ({len(names)}): {', '.join(names)}")
        current = server.request("tools/call", {"name": "list_past_sessions", "arguments": {"limit": 0}})
        sessions.add(str(cast(Message, tool_output(current))["current_session"]))

        for name, args in calls:
            result = server.request("tools/call", {"name": name, "arguments": args})
            failed = bool(result.get("isError"))
            output = tool_output(result)
            if name == "start_new_session" and isinstance(output, dict):
                sessions.add(str(cast(Message, output)["session"]))
            ok = ok and not failed
            print(f"\n{'ERROR ' if failed else ''}{name} {json.dumps(args)}")
            print(json.dumps(output, indent=2, ensure_ascii=False))
    finally:
        server.close()
    return ok


def main() -> int:
    sessions: set[str] = set()
    try:
        ok = run(parse_calls(sys.argv[1:]), sessions)
    except (SmokeError, OSError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        ok = False
    finally:
        created = [path for path in (SESSIONS_DIR / f"{session}.jsonl" for session in sessions) if path.is_file()]
        for path in created:
            path.unlink()
        if created:
            print(f"\n(deleted {len(created)} session log(s) this run created)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
