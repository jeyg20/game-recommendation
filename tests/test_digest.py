from datetime import date
from pathlib import Path

import pytest

from src import cli, config, digest

DAY = date(2026, 11, 2)


def pick(appid: int, name: str, **extra: object) -> dict[str, object]:
    return {"name": name, "appid": appid, "score": 0.5, "positive_ratio": 0.9, "matched_tags": ["Puzzle"], **extra}


def result(rec_ids: list[int], wild_ids: list[int] | None = None, backlog: int = 0) -> dict[str, object]:
    return {
        "applied": {"avoid_tags": ["Horror"]},
        "recommendations": [pick(appid, f"Game {appid}") for appid in rec_ids],
        "wildcards": [pick(appid, f"Wild {appid}", via_tag="Cozy") for appid in wild_ids or []],
        "backlog": [pick(900 + n, f"Owned {n}", playtime_hours=0.1) for n in range(backlog)],
    }


def test_first_digest_lists_everything_as_new() -> None:
    text = digest.to_markdown(result([1, 2], [3]), None, DAY)
    assert text.startswith("# Gamer Guide digest, 2026-11-02\n\nFirst digest: every pick is new.")
    assert "[Game 1]" in text and "[Game 2]" in text and "[Wild 3]" in text
    assert "| Via tag |" in text
    assert "- **Avoid:** Horror" in text
    assert text.rstrip().endswith("Previous digest: none")


def test_only_unseen_picks_are_new() -> None:
    previous: digest.DigestState = {"date": "2026-10-26", "appids": [1, 3]}
    text = digest.to_markdown(result([1, 2], [3, 4]), previous, DAY)
    new_part = text.split("Still recommended")[0]
    assert "[Game 2]" in new_part and "[Wild 4]" in new_part
    assert "[Game 1]" not in new_part and "[Wild 3]" not in new_part
    assert "Still recommended: Game 1" in text
    assert "Nothing new" not in text and "First digest" not in text
    assert text.rstrip().endswith("Previous digest: 2026-10-26")


def test_nothing_new_says_so() -> None:
    same = result([1, 2], [3])
    text = digest.to_markdown(same, digest.state(same, date(2026, 10, 26)), DAY)
    assert "Nothing new since 2026-10-26." in text
    assert "New for you" not in text and "New wildcards" not in text


def test_backlog_is_capped_and_not_diffed() -> None:
    res = result([1], backlog=8)
    previous = digest.state(res, date(2026, 10, 26))
    assert all(appid < 900 for appid in previous["appids"])
    text = digest.to_markdown(res, previous, DAY)
    assert "## Start from your backlog" in text
    assert "[Owned 4]" in text and "[Owned 5]" not in text


def test_state_keeps_order_without_duplicates() -> None:
    assert digest.state(result([2, 1], [1, 5]), DAY) == {"date": "2026-11-02", "appids": [2, 1, 5]}


@pytest.mark.parametrize("data", [None, [], {"date": "x"}, {"appids": [1]}, {"date": 3, "appids": []}])
def test_damaged_state_counts_as_first_digest(data: object) -> None:
    assert digest.parse_state(data) is None


def test_two_runs_in_a_row(isolated_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The acceptance check: the second run says nothing is new. Default path lands in data/output/."""
    monkeypatch.setattr(cli, "ranking", lambda: result([1, 2], [3], backlog=2))
    first = isolated_data / "output" / "first.md"
    cli.digest_report(first)
    cli.digest_report(None)
    today = date.today().isoformat()
    second = config.DIGEST_STATE_PATH.parent / f"digest-{today}.md"
    assert "First digest" in first.read_text(encoding="utf-8")
    assert f"Nothing new since {today}." in second.read_text(encoding="utf-8")
    assert digest.load_state() == {"date": today, "appids": [1, 2, 3]}
    assert sorted(path.name for path in isolated_data.rglob("*") if path.is_file()) == sorted(
        ["first.md", second.name, "digest_state.json"]
    )


def test_wrong_extension_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "ranking", lambda: pytest.fail("ranked before checking the extension"))
    with pytest.raises(SystemExit, match=r"\.md"):
        cli.digest_report(tmp_path / "digest.html")
    assert not config.DIGEST_STATE_PATH.exists()
