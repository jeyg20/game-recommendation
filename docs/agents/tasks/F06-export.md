# F06: Export a report

**Branch:** `feat/f06-export` off `ai-created` · **Wave:** 1 · **Needs:** T00

## Goal
A shareable file of the current picks: `uv run gamer-guide --export picks.md` (or `.html`), with store links.

## Today
`cli.report()` calls `tools.recommend_games(limit=15)`, prints `ui.recommendation_tables(result)` (ANSI, terminal
only) and saves the raw JSON to `data/output/recommendations.json`.

## Design
- New module `src/export.py`: `to_markdown(result) -> str` and `to_html(result) -> str` for a `recommend_games`
  result: title with the date, what steered it (`applied`), a recommendations table (rank, linked name
  `https://store.steampowered.com/app/<appid>`, fit, liked %, matched tags) and a backlog table (hours instead of
  liked). Any extra fields other tasks add (price, wishlist) are shown when present, ignored when not. HTML is one
  self-contained file with inline CSS and no scripts; escape every string.
- `cli.py`: `--export PATH` (format from the extension, `.md` or `.html`), mutually exclusive with the other modes.
  It reuses the `report()` ranking (saved preferences applied).
- F09 will import `to_markdown`; keep it a pure function.

## Touches
New `src/export.py`, `cli.py` (`main` arguments and a small function), README usage block.

## Acceptance
- [ ] `.md` renders as tables on GitHub; `.html` opens in a browser with working links.
- [ ] Unknown extension: a clear error, no file written.
- [ ] Names with `|`, `<`, `&` don't break either format.

## Proof
Tests on a fixed fake result (golden strings or structural checks). Live: export both formats and paste the first
lines of the `.md`.

## Out of scope
An MCP tool for it, PDF, images.
