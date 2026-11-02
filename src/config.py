"""Env values and data file paths, loaded once. Paths are anchored to the repo root, so it runs from any directory."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or ""
GEMINI_API: str | None = os.getenv("GEMINI_API")

# Owned library pipeline (main.py), one cache per step
STEAM_DATA_FILE_PATH = ROOT / "data/raw/steam_api_data.json"
STEAMSPY_DATA = ROOT / "data/raw/steamspy_data.json"
HLTB_DATA_FILE_PATH = ROOT / "data/raw/hltb_game_data_list.json"
ACHIEVEMENTS_PATH = ROOT / "data/raw/achievements.json"  # appid -> [achieved, total] | null (library.py)
WISHLIST_PATH = ROOT / "data/raw/wishlist.json"  # the player's wishlist, refetched daily (wishlist.py)
FRIENDS_PATH = ROOT / "data/raw/friends.json"  # friends' names and owned appids, refetched daily (friends.py)

# Steam-wide tag corpus (tag_profile.py)
STEAM_BACKGROUND_CORPUS = ROOT / "data/corpus/steam_background_corpus.json"
DF_TABLE_PATH = ROOT / "data/corpus/df_table.json"

# Recommendation stage (recommend.py)
TAG_POOLS_PATH = ROOT / "data/candidates/tag_pools.json"
CANDIDATE_HLTB_PATH = ROOT / "data/candidates/hltb_candidates.json"  # appid -> {hours, fetched_at} (constraints.py)
RECOMMENDATIONS_PATH = ROOT / "data/output/recommendations.json"

# Offline evaluation (evaluate.py): one JSON per run
EVAL_DIR = ROOT / "data/output/eval"

# Assistant (ai_agent.py)
PREFERENCES_PATH = ROOT / "data/preferences.json"
FEEDBACK_PATH = ROOT / "data/feedback.json"  # liked / disliked / not-interested games (feedback.py)
SESSIONS_DIR = ROOT / "data/logs/sessions"  # one JSONL log per chat session
DIGEST_STATE_PATH = ROOT / "data/output/digest_state.json"  # last digest: {date, appids} (digest.py)
