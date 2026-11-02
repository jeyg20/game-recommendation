# Coordinator board

How the feature work is split, in what order it lands, and who is doing what. One agent per task, each on its own
branch and worktree. The coordinator (you, or a Claude session you put in charge) launches agents, reviews their
hand-backs, merges into `ai-created` in the order below, and keeps this board current.

Rules every task agent follows are in [`AGENTS.md`](../../AGENTS.md). Task specs are in [`tasks/`](tasks/).

## Status board

Status: `todo` → `running` → `review` → `merged` (or `blocked: <why>`). Update the row when it changes.

| ID | Task | Wave | Needs | Branch | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | [Test harness and a green gate](tasks/T00-test-harness.md) | 0 | – | `feat/t00-test-harness` | merged | `6760961`; review fix: smoke deletes only its own logs |
| T01 | [Offline evaluation (hide your own games)](tasks/T01-evaluation.md) | 0 | T00 | `feat/t01-evaluation` | todo | |
| B01 | [Stale tag pools when SteamSpy returns nothing](tasks/B01-stale-tag-pools.md) | 1 | T00 | `feat/b01-stale-tag-pools` | todo | bug: top tags drop out; only `recommend.fetch_tag_pools` |
| F01 | [Ratings and "not interested"](tasks/F01-ratings.md) | 1 | T00 | `feat/f01-ratings` | todo | |
| F02 | [Did past picks work?](tasks/F02-pick-outcomes.md) | 1 | T00 | `feat/f02-pick-outcomes` | todo | |
| F03 | [Wishlist](tasks/F03-wishlist.md) | 1 | T00 | `feat/f03-wishlist` | todo | |
| F04 | [Prices and sales](tasks/F04-prices.md) | 1 | T00 | `feat/f04-prices` | todo | |
| F05 | [Explain one pick](tasks/F05-explain-pick.md) | 1 | T00 | `feat/f05-explain-pick` | todo | |
| F06 | [Export a report](tasks/F06-export.md) | 1 | T00 | `feat/f06-export` | todo | |
| R01 | [Variety in the top picks](tasks/R01-diversity.md) | 2 | T01 | `feat/r01-diversity` | todo | |
| R02 | [Abandoned games count against their tags](tasks/R02-abandonment.md) | 2 | T01 | `feat/r02-abandonment` | todo | |
| R03 | [Recent tastes count more](tasks/R03-recency.md) | 2 | R02 | `feat/r03-recency` | todo | |
| R04 | [Wildcard picks outside the bubble](tasks/R04-wildcards.md) | 2 | R02 | `feat/r04-wildcards` | todo | |
| R05 | [Length and price limits](tasks/R05-constraints.md) | 2 | F04, R04 | `feat/r05-constraints` | todo | |
| F07 | [Achievements in the taste weight](tasks/F07-achievements.md) | 2 | R03 | `feat/f07-achievements` | todo | |
| F08 | [What do my friends own?](tasks/F08-friends.md) | 3 | wave 1 | `feat/f08-friends` | todo | optional |
| F09 | [Weekly digest](tasks/F09-digest.md) | 3 | F06 | `feat/f09-digest` | todo | optional |

## Waves

- **Wave 0, one at a time.** T00 first: every later task proves itself with its tests and `scripts/mcp_smoke.py`.
  T01 right after, because wave 2 is judged by its numbers.
- **Wave 1, in parallel.** B01 (a bug fix, start it now: it doesn't need T01) and six features that mostly add new modules and tools. Start them all once T00 is merged.
- **Wave 2, mostly one at a time.** Ranking changes. They all touch the profile or `recommend()`, so overlapping
  rows in the file map below don't run at the same time. R01 can run alongside R02. R04 and R03 wait for R02,
  F07 for R03, R05 for F04 (merged in wave 1) and R04.
- **Wave 3, optional.** Start only after wave 1 has merged.

## Merge order

Merge into `ai-created` with a fast-forward or a merge commit. Before each merge, rebase the branch onto the
latest `ai-created` and rerun its gate. The order keeps conflicts small:

1. T00, T01
2. B01 (first: it fixes the candidate pools every later check runs on), then F05, F06, F02 (they touch no shared
   function, only the registries)
3. F01, then F04, then F03 (all three edit `tools.recommend_games` / `compact`; rebase each onto the previous one)
4. R02, R01, R04, R03, R05, F07
5. F08, F09

## File map: where tasks collide

`●` = edits a function or section, `+` = append-only edit in a shared registry. Two `●`s in one column means those
tasks must not run in parallel, or the second one rebases onto the first one's merge.

| File / place | F01 | F02 | F03 | F04 | F05 | F06 | R01 | R02 | R03 | R04 | R05 | F07 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tools.recommend_games` | ● | | ● | ● | | | | ● | | ● | ● | |
| `tools.compact` / `Recommendation` | | | ● | ● | | | | | | | ● | |
| `tools.ensure_loaded` (weights), `get_taste_profile` | | | | | | | | ● | ● | | | ● |
| `tag_profile.taste_weight` / `build_profile` | | | | | | | | ● | ● | | | ● |
| `tag_profile.adjust_profile` | ● | | | | | | | | | | | |
| `recommend.recommend` / `ranked` | | | | ● | | | ● | | | ● | ● | |
| `tools.RequestDraft` / `note_request` | | | | | | | | | | | ● | |
| `library.py` | | | | | | | | | | | | ● |
| `ui.py` tables | | | | ● | | | | | | ● | ● | |
| `steam_api.py` | | | ● | | | | | | | | | ● |
| `cli.py` | | | | | | ● | | | | | | |
| Registries (`TOOLS`, `ANNOTATIONS`, `TOOL_LABELS`, README table) | + | + | + | + | + | + | | | | | | |
| Prompts (`SYSTEM_PROMPT`, `INSTRUCTIONS`) | + | + | + | + | + | | | | | + | + | |
| `config.py` paths | + | | + | | | | | | | | + | + |

Registry conflicts (two branches appending to `TOOLS`) are expected and trivial: keep both lines.

## Launching a task agent

1. Create the worktree: `scripts/agent_worktree.sh <id> <slug>`, e.g. `scripts/agent_worktree.sh f01 ratings`.
   It branches off `ai-created` and copies `.env` and `data/` so agents never share cache files.
2. Start a Claude Code session in `../gamer_guide-<id>` with this prompt:

   > You are the agent for task `<ID>` in Gamer Guide. Read `AGENTS.md`, then `docs/agents/tasks/<ID>-*.md`.
   > Implement exactly that task on the current branch (`feat/<id>-<slug>`), meet its acceptance criteria, run
   > the gate and the proof it lists, commit, and hand back as `AGENTS.md` describes. Don't edit
   > `docs/agents/COORDINATOR.md`. If the spec is wrong about the code, say so in the hand-back instead of
   > working around it.

3. Set the row to `running`.

## Reviewing a hand-back

1. Check the hand-back has gate output and the task's proof, and that the diff stays inside the task's files
   (see its "Touches" list and the file map). Shared-file edits must be append-only.
2. Review with a Claude reviewer subagent (never GPT): brief it with the task file and `git diff ai-created...`.
3. Rebase onto `ai-created`, rerun the gate, merge, push.
4. Set the row to `merged`. Update the README if the hand-back says user-facing behaviour changed, and the
   Obsidian vault (`/mnt/d/Obsidian_notes/claude_brain/game_recommendation`, see its `Docs workflow.md`).
5. Remove the worktree: `scripts/agent_worktree.sh --remove <id>`.

## Writing a new task

Copy [`TASK_TEMPLATE.md`](TASK_TEMPLATE.md) into `tasks/`, add a board row, and add its column to the file map.
