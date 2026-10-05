"""HowLongToBeat lookup: best name match for a game."""

import asyncio

from howlongtobeatpy import HowLongToBeat, HowLongToBeatEntry

from src.ui import status, style


async def get_game_hltb(game_name: str) -> HowLongToBeatEntry | None:
    """Search HLTB and return the result most similar to `game_name`, or None if nothing matched or the search failed."""
    try:
        results_list = await HowLongToBeat().async_search(game_name, similarity_case_sensitive=False)

        if results_list is not None and len(results_list) > 0:
            best_element = max(results_list, key=lambda element: element.similarity)
            return best_element
    except Exception as e:
        status.say(style(f"  ! HLTB search failed for {game_name!r}: {e}", "yellow"))


async def main() -> None:
    game_info = await get_game_hltb("Portal 2")
    if game_info is None:
        print("No HLTB match for Portal 2")
        return
    print(
        game_info.game_name,
        game_info.main_story,
        game_info.review_score,
    )


if __name__ == "__main__":
    asyncio.run(main())
