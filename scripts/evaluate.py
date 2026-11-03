#!/usr/bin/env -S uv run python
"""Score the ranking offline: hide each of the player's strongest or most recent games and report where it comes back.

    scripts/evaluate.py --label "before R01"
    scripts/evaluate.py --set recent    # one set only, for a quick tuning run

Prints a summary line and a per-game table per set (favourites, recent), and writes the full result to
data/output/eval/<timestamp>.json.
Uses the cached library and df table; tag pools are cached for a day, so runs on the same day are comparable.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `src` imports when run as a plain script

from src import library, tools
from src.evaluate import EVAL_SETS, EvalSet, HeldOutGame, Summary, evaluate, save
from src.ui import status


def summary_line(s: Summary) -> str:
    diversity = "n/a" if s["diversity"] is None else f"{s['diversity']:.3f}"
    return (
        f"recall@10 {s['recall_at_10']:.2f}  recall@25 {s['recall_at_25']:.2f}  MRR {s['mrr']:.3f}  "
        f"never retrieved {s['never_retrieved']}/{s['held_out']}  diversity {diversity}"
    )


def game_table(games: list[HeldOutGame]) -> str:
    lines = [f"{'rank':>5}  {'weight':>6}  name"]
    for game in games:
        rank = "-" if game["rank"] is None else str(game["rank"])
        lines.append(f"{rank:>5}  {game['taste_weight']:>6.3f}  {game['name']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="", help="stored in the JSON, to tell before/after runs apart")
    parser.add_argument("--set", choices=(*EVAL_SETS, "both"), default="both", help="which held-out set to run")
    args = parser.parse_args()
    sets: tuple[EvalSet, ...] = EVAL_SETS if args.set == "both" else (args.set,)

    with status.running("evaluating the ranking"):
        player_games, games, df_table, _ = tools.ensure_loaded()
        result = evaluate(
            {game["appid"] for game in player_games},
            games,
            df_table,
            label=args.label,
            last_played=tools.last_played_by_appid(player_games),
            recent_appids=tools.played_recently(player_games),
            achievement_share=library.load_achievement_shares(player_games),
            sets=sets,
        )
        path = save(result)

    for name, summary, held in (
        ("favourites", result["summary"], result["games"]),
        ("recent", result["recent_summary"], result["recent_games"]),
    ):
        if summary is not None:
            print(f"{name}: {summary_line(summary)}")
            print(game_table(held) + "\n")
    print(f"written to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
