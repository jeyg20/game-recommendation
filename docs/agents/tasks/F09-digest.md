# F09: Weekly digest

**Branch:** `feat/f09-digest` off `ai-created` · **Wave:** 3 (optional) · **Needs:** F06 merged

## Goal
A short Markdown file the player can read weekly: new games that fit since the last digest, and the best backlog
games to start.

## Today
After F06, `export.to_markdown(result)` renders a `recommend_games` result. `cli.report()` ranks with saved
preferences applied.

## Design
- `gamer-guide --digest [PATH]` (default `data/output/digest-<date>.md`): run the same ranking as `--report`, then
  compare with the appids in `data/output/digest_state.json` (path in `config.py`, `{date, appids}`). The digest
  lists **new** picks first, then up to 5 backlog games, then the date of the previous digest. Update the state
  file after writing.
- First run: everything is new; say so.
- Scheduling isn't part of the code. In the README, show a cron line and mention that a Claude Code scheduled
  task can run it too. Don't install either.

## Touches
`cli.py`, `config.py`, a new `src/digest.py` (the diff and the Markdown, reusing `export.to_markdown` pieces),
README, tests.

## Acceptance
- [ ] Two runs in a row: the second says nothing is new.
- [ ] Doesn't modify anything outside `data/output/` (tag pool / corpus caches may grow as in any ranking run).

## Proof
Tests for the diff logic. Live: two consecutive runs, paste both files' first lines.

## Out of scope
Email or notifications, installing a cron job or scheduled task.
