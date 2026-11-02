# Agent guide: Gamer Guide

Rules for any coding agent working in this repo. If you were given a task, also read your task file in
`docs/agents/tasks/` and the board in `docs/agents/COORDINATOR.md`.

## What this is

A Steam game recommender for one player. It builds a taste profile from the player's Steam playtime, ranks games
they don't own against it, and talks to them through either the Gemini CLI (`src/cli.py` + `src/ai_agent.py`) or
the MCP server (`src/mcp_server.py`, Claude does the reasoning). Both frontends call the same tools in
`src/tools.py`. The README's "Pipeline" section explains the maths; read it before touching ranking code.

| Module | Owns |
| --- | --- |
| `config.py` | Env values and every data file path (add new paths here) |
| `storage.py` | All JSON/JSONL file I/O (`read_json`, `write_json`, `append_jsonl`, `load_or_build`) |
| `steam_api.py`, `steamspy.py`, `hltb.py` | Thin HTTP clients. Return `None` on failure, never raise |
| `library.py` | Owned games, enriched with SteamSpy tags and HLTB times, cached under `data/raw/` |
| `tag_profile.py` | Corpus, idf table, `taste_weight`, `tag_vector`, `build_profile`, `adjust_profile`, `cosine` |
| `recommend.py` | Candidate pools, shovelware floor, finalists, scoring, required/avoided tag rules |
| `preferences.py` | Saved cross-session steers (`data/preferences.json`) |
| `history.py` | Session logs (`data/logs/sessions/*.jsonl`) |
| `tools.py` | The tool functions both frontends call, plus shared `state` |
| `ai_agent.py`, `cli.py`, `ui.py` | Gemini chat, terminal UI |
| `mcp_server.py` | MCP frontend: instructions, annotations, lock, logging |

## Branches

- `ai-created` is the integration branch. Branch your task off it as `feat/<task-id>-<slug>`
  (e.g. `feat/f01-ratings`). Open your PR, or hand back, against `ai-created`.
- Never commit to `main`. It is the owner's hand-written line.
- Never force-push a branch that isn't yours. Rebase your own branch onto `ai-created` before handing back.

## Agents

Run every subagent and reviewer inside the Claude ecosystem. Never delegate to GPT, Codex, Sol or anything that
uses the OpenAI API (that includes the `sol-loop` skill's reviewer and `codex:*` skills).

## Gate: run before every commit

```sh
uv run pytest -q
uvx ruff check src tests
uvx ruff format --check --line-length 120 src tests
uvx pyright --pythonpath .venv/bin/python src
```

All four must pass (they did on `ai-created` when T00 merged). Run ruff on `scripts` too if you touched it.

## Adding or changing a tool

A tool is a plain function in `src/tools.py` (or a module it imports) that returns JSON-serializable data. Its
signature and docstring become the schema both models read, so the `Args:` section must say when to use it.
A new tool has to be registered in **four** places, or one frontend silently won't have it:

1. `tools.TOOLS` (append, don't reorder)
2. `mcp_server.ANNOTATIONS` (read-only / open-world hints). A tool missing here is **not served over MCP**
3. `ai_agent.TOOL_LABELS` (status-line text)
4. The README tool table

If the model should use it in a particular way, add one line to both `ai_agent.SYSTEM_PROMPT` and
`mcp_server.INSTRUCTIONS`. These four registries and the two prompts are shared by every task: make small,
append-only edits there so parallel branches merge cleanly.

## Data and network rules

- `data/` is the player's real data and isn't in git. Never delete or overwrite `data/raw/`, `data/corpus/`,
  `data/candidates/` or `data/preferences.json`. Tests must point `config` paths at `tmp_path`.
- New persistent data goes in a new file under `data/`, with its path in `config.py` and I/O through `storage.py`.
- Don't call `refresh_library` or `update_background_corpus` while testing: they rewrite the caches and take
  minutes to over an hour.
- SteamSpy allows 1 request per second (`all` 1 per 60 s). Keep the existing `time.sleep(1)` pacing, and cache.
- Tests are offline: fixtures, no HTTP. A live check may run `recommend_games` once (tag pools are cached for a day).
- If a live check creates a session log under `data/logs/sessions/`, delete the file afterwards.
- Never print or commit `.env` values (Steam key, Steam ID, Gemini key).

## Proof

Every task file lists its proof. On top of that:

- Pure logic changes: a test that fails without your change.
- Tool changes: call the tool over MCP with `scripts/mcp_smoke.py <tool> '<json args>'` and paste the output in
  your hand-back. It deletes only its own session logs.
- Ranking changes: run `scripts/evaluate.py` (T01) before and after, and report both numbers.

## Style

Match the surrounding code: typed (`TypedDict`s for data shapes), small functions, docstrings that say *why*,
module-level constants for tunables, 120-column lines, `status.update` / `status.say` for progress, never `print`
inside library code. Keep diffs minimal. Don't refactor outside your task.

## Hand-back

End with: branch name, head SHA, gate output, proof output, files touched, and any shared-file edits (registries,
prompts, README). Don't update the Obsidian docs vault; the coordinator does that after merging.
