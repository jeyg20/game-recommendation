import os

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field

load_dotenv()
gemini_api = os.getenv("GEMINI_API")


def generate_content(gamer_info: str):
    client = genai.Client(api_key=gemini_api)
    prompt = """The following information belongs to a gamer, it holds data of the games and categories they have play
    and enojoyed the most, I want you to analaized this data and recommend the next game they should play, the gamer
    information also has the list of games they owned but haven't play yet, if any of this games allign with the user
    taste given the previous information use them as the primal recommendation with some other unowned games
    """
    response = client.models.generate_content(model="gemini-3.1-flash-lite", contents=f"{prompt}\n\n{gamer_info}")

    return response.text


class GameCategories(BaseModel):
    name: str = Field(description="Name of the game")
    categories: list[str] = Field(description="categories of the game")
