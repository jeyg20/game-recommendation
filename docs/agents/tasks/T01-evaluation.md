# T01: Offline evaluation (hide your own games)

**Branch:** `feat/t01-evaluation` off `ai-created` · **Wave:** 0 · **Needs:** T00

## Goal
A number that says whether a ranking change made the picks better, instead of eyeballing them. Every wave 2 task
reports it before and after.

## Today
The tunables (`MIN_REVIEWS`, `FINALISTS`, `AVOID_TOP_TAGS`, `VOLUME_SCALE_HOURS`, `REFERENCE_WEIGHT`, ...) were
set by hand. `recommend.recommend(profile, top, excluded_appids, df_table, ...)` takes the profile as an argument,
so it can be called with a profile built from part of the library.

## Design
Leave-one-out over the player's strongest games:
1. Load the library and df table (`tools.ensure_loaded` pieces). Rank owned games by `taste_weight`.
2. Pick the held-out set: the top `HOLDOUT_GAMES = 10` games that have tags and pass `passes_floor` (they must be
   findable at all, or the metric measures retrieval coverage, not ranking).
3. For each held-out game: build the profile without it, call `recommend` with
   `excluded = owned - {held_out}` and the same `top_tags(profile, 8)` the tool uses, and record the held-out
   game's rank in the result (None if absent).
4. Report: recall@10, recall@25, mean reciprocal rank, how many were never retrieved, and **diversity**: the mean
   pairwise cosine between the top 10 picks' tag vectors for the full profile (lower = more varied; R01 needs it).

`scripts/evaluate.py` prints one summary line plus a per-game table, and writes the full result to
`data/output/eval/<timestamp>.json`. `--label <text>` is stored in the JSON so before/after runs are easy to tell
apart. Put the logic in `src/evaluate.py` (importable, testable) and keep the script thin.

## Touches
New `src/evaluate.py`, `scripts/evaluate.py`, `config.py` (eval output dir), `tests/test_evaluate.py`, README
("Development" section).

## Acceptance
- [ ] Deterministic: two runs in a row give identical numbers (pools are cached for a day).
- [ ] Doesn't modify `data/raw/`, `data/preferences.json` or session logs. (Tag pool and corpus caches may grow;
      that's the normal pipeline.)
- [ ] Runs in a few minutes with warm caches.

## Proof
Two consecutive runs with identical output. A unit test with a fake `recommend` that checks the rank and metric
maths.

## Out of scope
Changing any tunable. Online metrics (that's F02).
