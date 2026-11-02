# T00: Test harness and a green gate

**Branch:** `feat/t00-test-harness` off `ai-created` · **Wave:** 0 · **Needs:** –

## Goal
Every later task needs a way to prove its change: offline tests, a gate that passes, and a script that calls a
tool over MCP. None of that exists yet.

## Today
- No tests, no pytest. `pyproject.toml` has no dev dependencies.
- The gate in `AGENTS.md` fails on code that was already there: ruff `BLE001` at `ai_agent.py:108` and `hltb.py:18`,
  `ui.py` not formatted, pyright `reportRedeclaration` at `ai_agent.py:96` (the `call` parameter is reassigned) and
  three `Counter[str]` float assignments in `tag_profile.py` (~272, ~301, ~305).
- Dead code: `tag_profile.test_func` (and the `__main__` block that calls it) and the `reclassify_cache` stub.
- A throwaway MCP driver was used to prove the MCP server (raw JSON-RPC over stdio, every stdout line must parse).

## Design
- `uv add --dev pytest`. `tests/` at the repo root, `tests/conftest.py` with fixtures:
  - `df_table`: a small hand-made `DfTable` (5-10 tags, `n`, `df`, `idf`).
  - `isolated_data` (autouse): monkeypatch every path constant in `src.config` *and* the modules that imported them
    by name (`recommend.TAG_POOLS_PATH`, `recommend.STEAM_BACKGROUND_CORPUS`, `history.SESSIONS_DIR`,
    `preferences.PREFERENCES_PATH`, ...) to `tmp_path`, so no test can touch `data/`.
  - `fresh_state`: reset `tools.state` and `tools.reference_cache` between tests.
- Tests, one file per module, for: `taste_weight`, `is_open_ended`, `tag_vector`, `build_profile`,
  `adjust_profile`, `cosine`, `top_tags`, `passes_floor`, `pick_finalists`, `has_required_tags`, `has_avoided_tag`,
  `fits_tags`, `recommend` (monkeypatch `fetch_tag_pools` and `enrich_tags`: strict, relaxed and unenforced paths),
  `is_backlog`, `canonical_tags`, `merge` / `without`, `note_request` + `mcp_server.start_new_session` (draft reset).
- Fix the baseline gate. Keep both blind `except`s (they are deliberate boundaries) with `# noqa: BLE001` and a
  reason. Rename the redeclared variable. Annotate the counters as `Counter[str]` holding floats correctly
  (e.g. `defaultdict(float)` or a `dict[str, float]`), without changing results.
- Delete `test_func`, its `__main__` block and the `reclassify_cache` stub. Keep the README "Open ideas" line about
  reclassifying (it's still an idea).
- `scripts/mcp_smoke.py`: spawns `uv run gamer-guide-mcp`, initializes, lists tools, and calls the tools given on
  the command line, e.g. `scripts/mcp_smoke.py get_taste_profile '{"top_n": 5}' look_up_game '{"name": "Hades"}'`.
  It prints each result, exits non-zero on a tool error or any stdout line that isn't JSON-RPC, and deletes the
  session log the run created.

## Touches
`pyproject.toml`, `uv.lock`, `tests/**`, `scripts/mcp_smoke.py`, and the lines named above in `ai_agent.py`,
`hltb.py`, `ui.py`, `tag_profile.py`. README: a "Development" section with the gate and smoke commands.

## Acceptance
- [ ] The full gate in `AGENTS.md` passes.
- [ ] `uv run pytest -q` runs offline (unplug the network or monkeypatch `requests.get` to raise in conftest).
- [ ] `scripts/mcp_smoke.py get_taste_profile '{}'` prints the profile and exits 0.
- [ ] The baseline fixes don't change behaviour: `uv run gamer-guide --report` gives the same top 10 before/after.

## Proof
Gate output, pytest summary, the smoke output, and the before/after `--report` top 10.

## Out of scope
New features, refactors beyond the gate fixes, CI config.
