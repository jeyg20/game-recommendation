"""Gemini client that turns a gamer's library summary into a recommendation."""

from google import genai
from pydantic import BaseModel, Field

from src import config


def generate_content(gamer_info: str) -> str | None:
    """Ask Gemini for the next game to play; returns the response text (None if the model returned no text)."""
    client = genai.Client(api_key=config.GEMINI_API)
    prompt = """The following information belongs to a gamer, it holds data of the games and categories they have play
    and enojoyed the most, I want you to analaized this data and recommend the next game they should play, the gamer
    information also has the list of games they owned but haven't play yet, if any of this games allign with the user
    taste given the previous information use them as the primal recommendation with some other unowned games
    """
    response = client.models.generate_content(model="gemini-3.1-flash-lite", contents=f"{prompt}\n\n{gamer_info}")

    return response.text


class GameCategories(BaseModel):
    """Structured-output schema: a game and its categories."""

    name: str = Field(description="Name of the game")
    categories: list[str] = Field(description="categories of the game")
