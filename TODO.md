# MCP server TODO

Status (2026-11-01): built by Claude on request ("full send"), see `src/mcp_server.py` and the README's MCP section.
Decisions: session set in `main()` before `run()`, and `start_new_session` (served, immediate reset) at the start of
each conversation, since the process can outlive one; each tool call logged as a turn
(`[mcp] <tool>`); all calls serialized by one lock; picks shown as Markdown tables per the instructions; the SDK's
stdio transport already diverts fd 1 to stderr, so no `ui.py` change was needed (proved with a tool that prints).
Left: approve the server in `claude` and run a real chat; progress reporting (option B).

Goal: expose Gamer Guide's tools over MCP so Claude Code / Claude Desktop (subscription) does the reasoning
instead of the paid Gemini API. The CLI keeps working as-is; the server is a second frontend.

Rule: I write every line. Claude explains, points at the SDK, and reviews.

SDK facts (checked against `mcp` 2.3.0):
- v2 renamed `FastMCP` -> `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Most tutorials show v1.
- A plain `def` tool runs on a worker thread (`anyio.to_thread.run_sync`), so `asyncio.run` inside
  `load_library()` / `refresh_library()` is fine. Keep tools sync.
- Useful API: `MCPServer(name=..., instructions=..., lifespan=...)`, `server.add_tool(fn, annotations=...)`,
  `server.run()` (stdio by default), `Context.report_progress(progress, total, message)`.

## 0. Setup
- [x] `uv add mcp`
- [ ] Skim the v2 migration guide: https://py.sdk.modelcontextprotocol.io/v2/migration/

## 1. Bare server that connects
- [x] `src/mcp_server.py`: an `MCPServer` with a name, and a `main()` that runs it
- [x] Register one tool (`get_taste_profile`)
- [x] Register in Claude Code: the repo's `.mcp.json` (`uv run --project . gamer-guide-mcp`)
- [ ] `/mcp` in Claude Code shows it connected; Claude can call the tool and see my real profile
- [ ] Try the MCP Inspector (`uv run mcp dev src/mcp_server.py`) to call tools without spending usage

## 2. Register the tools
- [x] Register everything in `TOOLS` (docstrings become descriptions, type hints become the schema)
- [x] Re-read each docstring as Claude will: does it say when to use the tool?
- [x] Drop tools that don't fit MCP (`start_new_session`: a new chat is the reset)
- [x] Add `ToolAnnotations` (read-only vs writes files) so permission prompts make sense

## 3. Server-side state
- [x] Set up what `Assistant.new_session()` used to: `tools.state["session"]`, the draft
- [x] Decide: startup code before `run()`, or `lifespan`?
- [x] Question: one server process = one session? (Claude Code starts one per conversation)

## 4. Instructions
- [x] Pass an adapted `SYSTEM_PROMPT` as `instructions=`
- [x] Remove "the player already sees every result as a table" (no table is printed now)
- [x] Remove `start_new_session` mentions if dropped
- [x] Decide how picks are shown: `recommend_games` returns Markdown, or instructions tell Claude how to present them
      (`ui.recommendation_tables` emits ANSI codes: not usable as-is)

## 5. Clean stdout
- [x] stdout carries the protocol: any `print` during a tool call breaks it
- [x] `Status.say` falls back to `print` without a TTY (`src/ui.py:253`)
- [x] Check `status.update` / `status.running` paths used by the library steps
- [x] Verify by calling `refresh_library` from the Inspector

## 6. History logging
- [x] `log_turn` needs prompt + reply, which the server never sees
- [x] Decide: log tool calls only (steers and picks live in args/results), or don't log MCP sessions
- [x] Check what `picks_with_reasons` / `get_recommendation_history` actually read before deciding

## 7. Slow tools (last)
- [ ] `refresh_library`, `update_background_corpus` take minutes
- [x] Option A: raise `MCP_TOOL_TIMEOUT` in Claude Code
- [ ] Option B: progress via `ctx.report_progress` (async, but tools run on worker threads: design problem)

## 8. Concurrency
- [x] Claude can call tools in parallel, each on its own thread
- [x] `tools.state` is shared and `ensure_loaded` does check-then-set: what happens on a cold cache with two calls?

## Done when
- [ ] A full chat in Claude Code: describe what I want over a few messages, get picks, look up a game,
      read history, all without the Gemini key
