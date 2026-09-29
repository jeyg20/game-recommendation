"""Env values and data file paths, loaded once. Paths are relative to the repo root (run from there)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STEAM_API_KEY = os.getenv("STEAM_API_KEY") or ""
STEAM_ID = os.getenv("STEAM_ID") or ""
GEMINI_API: str | None = os.getenv("GEMINI_API")

# Owned library pipeline (main.py), one cache per step
STEAM_DATA_FILE_PATH = Path("data/raw/steam_api_data.json")
STEAMSPY_DATA = Path("data/raw/steamspy_data.json")
HLTB_DATA_FILE_PATH = Path("data/raw/hltb_game_data_list.json")

# Steam-wide tag corpus (tag_profile.py)
STEAM_BACKGROUND_CORPUS = Path("data/corpus/steam_background_corpus.json")
DF_TABLE_PATH = Path("data/corpus/df_table.json")
