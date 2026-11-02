"""HowLongToBeat lookup: best name match for a game."""

import asyncio

from howlongtobeatpy import HowLongToBeat, HowLongToBeatEntry

from src.ui import status, style


class HltbSearchFailed(Exception):
    """The HLTB search itself failed (an error, or no response), as opposed to finding no game."""


async def get_game_hltb(game_name: str, *, raise_on_failure: bool = False) -> HowLongToBeatEntry | None:
    """Search HLTB and return the result most similar to `game_name`, or None if nothing matched or the search failed.

    With `raise_on_failure`, a failed search raises HltbSearchFailed instead: a caller that caches misses (constraints)
    must not cache a network hiccup as "no time".
    """
    try:
        results_list = await HowLongToBeat().async_search(game_name, similarity_case_sensitive=False)
        if results_list is None:  # howlongtobeatpy's answer to a failed request; no match is an empty list
            raise HltbSearchFailed("no response")
    except Exception as e:  # any HLTB failure: a miss for the library build, an error for constraints
        status.say(style(f"  ! HLTB search failed for {game_name!r}: {e}", "yellow"))
        if raise_on_failure:
            raise HltbSearchFailed(game_name) from e
        return None
    return max(results_list, key=lambda element: element.similarity) if results_list else None


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
