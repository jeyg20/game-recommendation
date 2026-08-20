import asyncio
from typing import AnyStr

from howlongtobeatpy import HowLongToBeat


async def get_game_hltb(game_name: str):
    try:
        results_list = await HowLongToBeat().async_search(game_name, similarity_case_sensitive=False)

        if results_list is not None and len(results_list) > 0:
            best_element = max(results_list, key=lambda element: element.similarity)
            return best_element
    except Exception as e:
        print(f"Game not found error: {e}")


async def main():
    game_info = await get_game_hltb("Portal 2")
    print(
        game_info.game_name,
        game_info.main_story,
        game_info.review_score,
    )


if __name__ == "__main__":
    asyncio.run(main())
