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
| T01 | [Offline evaluation (hide your own games)](tasks/T01-evaluation.md) | 0 | T00 | `feat/t01-evaluation` | merged | `598ae98`. Baseline 2026-11-01 (after B01): recall@10 0.00, recall@25 0.20, MRR 0.015, never retrieved 5/10, diversity 0.766 |
| B01 | [Stale tag pools when SteamSpy returns nothing](tasks/B01-stale-tag-pools.md) | 1 | T00 | `feat/b01-stale-tag-pools` | merged | `6522173`. Follow-up: the empty-tag set lasts the process (a long-lived Desktop server never re-asks until restart) |
| F01 | [Ratings and "not interested"](tasks/F01-ratings.md) | 1 | T00 | `feat/f01-ratings` | merged | `60637c1`. Disliked / not-interested games stay hidden even with `use_saved_preferences=False`. Follow-up: `explain_pick` steered mode ignores ratings (fold into the `noted_taste` dedupe) |
| F02 | [Did past picks work?](tasks/F02-pick-outcomes.md) | 1 | T00 | `feat/f02-pick-outcomes` | merged | `ac0c027`. `pick_outcomes(games)` takes the library (avoids a circular import); `hit_rate` = bought and played over 2 h, plus `buy_rate`. First live numbers: 34 picks, 1 bought (Enshrouded, 0.7 h) |
| F03 | [Wishlist](tasks/F03-wishlist.md) | 1 | T00 | `feat/f03-wishlist` | merged | `af40649`. Live: 19 wishlisted, only 6 scorable (SteamSpy has no tags for 13). Names come from the store `appdetails`; cache in `wishlist.load_wishlist`. Follow-up: `enrich_tags` saves tagless games as permanent `excluded` corpus records, so they never get re-asked; save them as `failed` instead |
| F04 | [Prices and sales](tasks/F04-prices.md) | 1 | T00 | `feat/f04-prices` | merged | `e36c566`. `price_usd` is the price after any discount (SteamSpy `price`), not the list price. Follow-up: `--export` shows the two raw fields as columns, also on the backlog (always `-`); reuse `ui.price()` for one Price column |
| F05 | [Explain one pick](tasks/F05-explain-pick.md) | 1 | T00 | `feat/f05-explain-pick` | merged | `d60a0fc`. Follow-ups: `noted_taste` copies the steering in `recommend_games` (dedupe after F01/F03/F04); `steered=True` sees only the noted draft and saved preferences, not one-off `recommend_games` arguments; "Hades" resolves to Hades II (`find_game` takes the first store hit) |
| F06 | [Export a report](tasks/F06-export.md) | 1 | T00 | `feat/f06-export` | merged | `48c9e33`. `export.to_markdown` / `to_html` are pure (optional `day`); unknown pick fields become extra columns |
| R01 | [Variety in the top picks](tasks/R01-diversity.md) | 2 | T01 | `feat/r01-diversity` | merged | `d24f680`. λ 0.75. Evaluate 2026-11-02: diversity 0.766 → 0.445, recall@25 0.20 → 0.10 (−1 game, the limit), recall@10 0.00 → 0.10, MRR 0.014 → 0.024 |
| R02 | [Abandoned games count against their tags](tasks/R02-abandonment.md) | 2 | T01 | `feat/r02-abandonment` | merged | `8779ef1`. Evaluate 2026-11-02: recall@25 0.10 → 0.10, never retrieved 6 → 5/10, diversity 0.445 → 0.411. Product change: abandoned games leave the backlog (13 → 7). `is_abandoned` / `last_played_by_appid` / `backlog_games` live in `tools.py` (circular import). Follow-up: `explain.game_shares` still uses `taste_weight` (an abandoned game could show under because_you_played) |
| R03 | [Recent tastes count more](tasks/R03-recency.md) | 2 | R02 | `feat/r03-recency` | merged | `efd9fce`. Evaluate 2026-11-02: MRR 0.023 → 0.013, never retrieved 5 → 8/10, diversity 0.411 → 0.592. The hold-out test favours old favourites (half the held-out games last played 2–3 years ago); not tuned to it. `RECENCY_FLOOR = 1.0` turns it off |
| R04 | [Wildcard picks outside the bubble](tasks/R04-wildcards.md) | 2 | R02 | `feat/r04-wildcards` | merged | `a0a55d4`. Coordinator change: wildcards re-ranked with `diversify` (the spec's fit order gave three BioShocks). Only 1 of ranks 9–20 (Underwater) passes the median-idf filter on this profile, so wildcards come from one pool; widen `WILDCARD_TAG_RANKS` or loosen the filter if they feel narrow. Follow-up: `--export` has no wildcards section |
| R05 | [Length and price limits](tasks/R05-constraints.md) | 2 | F04, R04 | `feat/r05-constraints` | merged | `1ca579c`. Agent stopped before its hand-back; coordinator ran the gate and the live proof (24 cached HLTB entries, second call no lookups). Only the best `3 * limit` are length-checked, so a tight limit can return fewer than `limit` |
| F07 | [Achievements in the taste weight](tasks/F07-achievements.md) | 2 | R03 | `feat/f07-achievements` | merged | `b11d923`. MRR 0.013 → 0.014, recall unchanged (most heavy games already exceed their story time). Achievements are a separate cached step (`library.load_achievement_shares`); the main checkout builds `data/raw/achievements.json` on first load (~48 requests, ~1 min). Found and fixed after merge: failed Steam requests printed the key (`1de5118`) |
| F08 | [What do my friends own?](tasks/F08-friends.md) | 3 | wave 1 | `feat/f08-friends` | merged | `9cf5ee6`. optional. Live: 15 friends, 3 private, first call 95 s, cached 0.8 s. `steamids` added to the redacted params. Saved/noted steers aren't auto-applied (the model passes them) |
| C01 | [Follow-ups from the reviews](tasks/C01-cleanup.md) | 4 | R05 | `feat/c01-cleanup` | running | T3 child thread, alongside F09 (not export.py) |
| F09 | [Weekly digest](tasks/F09-digest.md) | 3 | F06 | `feat/f09-digest` | running | optional. T3 child thread, alongside R05 |

## Waves

- **Wave 0, one at a time.** T00 first: every later task proves itself with its tests and `scripts/mcp_smoke.py`.
  T01 right after, because wave 2 is judged by its numbers.
- **Wave 1, in parallel.** B01 (a bug fix, start it now: it doesn't need T01) and six features that mostly add new modules and tools. Start them all once T00 is merged.
- **Wave 2 "before" numbers.** SteamSpy answers change from day to day, so each wave 2 task runs its own
  `scripts/evaluate.py --label before` on `ai-created` the same day as its "after". Don't compare with the board's baseline.
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
| `tools.library_profile` / `ensure_loaded` (weights), `get_taste_profile` | | | | | | | | ● | ● | | | ● |
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

## Automated mode (T3 Code)

The coordinator session launches the agents itself, as T3 Code child threads, instead of you opening one session
per worktree.

- The T3 project has a **Worktree setup** script (`scripts/worktree_setup.sh`, run on worktree create). It copies
  `.env` and `data/` from the main checkout and runs `uv sync`. It skips a copy that already exists, so rerunning
  it is safe.
- Each agent is a `delegate_task` with `isolation: "worktree"`, Claude only, **effort at most `medium`**, and at
  most **two running at once** (the owner's limits). The brief is the launch prompt above. It also tells the agent
  to `git switch -c feat/<id>-<slug>` first, and to run `scripts/worktree_setup.sh` if `.env` or `data/` is missing.
- Signing: commits are SSH-signed. Agents commit with `SSH_AUTH_SOCK` set to the socket the owner gives. When
  signing fails, they leave the work staged and say so, and the coordinator commits it.
- When a child hands back, the coordinator reviews it as below, merges it, and starts the next task in merge order.

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
