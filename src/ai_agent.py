"""Gemini client that explains the ranked recommendations in plain language."""

import json

from google import genai

from src import config
from src.recommend import Recommendation

MODEL = "gemini-3.1-flash-lite"

PROMPT = """You are a game recommendation assistant. A local ranking already picked the games below by comparing
Steam user tags against a taste profile built from the player's own playtime. Do not re-rank or add games.

For each recommended game, write one or two sentences on why it fits this player, grounded in the matched tags and
the games they played most. Then do the same for the backlog: games they already own but never played, best fit first.
Keep it concise and skip games that look like a weak fit (low score).
"""


def explain(
    top_tags: list[str],
    most_played: list[str],
    recommendations: list[Recommendation],
    backlog: list[Recommendation],
    profile_text: str = "",
) -> str | None:
    """Ask Gemini to explain the top picks. `profile_text` is a seam for a bio/psych profile later (empty for now).

    Returns the response text, or None if the model returned no text.
    """
    client = genai.Client(api_key=config.GEMINI_API)
    gamer_info = {
        "profile_top_tags": top_tags,
        "most_played_games": most_played,
        "recommendations": recommendations,
        "backlog": backlog,
    }
    contents = f"{PROMPT}\n\n{json.dumps(gamer_info, indent=2)}"
    if profile_text:
        contents += f"\n\nAbout the player:\n{profile_text}"

    response = client.models.generate_content(model=MODEL, contents=contents)
    return response.text
