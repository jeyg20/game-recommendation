"""A recommend_games result as a shareable file: Markdown (renders on GitHub) or one self-contained HTML page.

Both renderers are pure functions of the result, so other code (F09) can reuse them. Every string from the result
is escaped: game names can hold `|`, `<` or `&`.
"""

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from src import ui

STORE_URL = "https://store.steampowered.com/app/{appid}"
TITLE = "Gamer Guide picks"

# what steered the ranking, in the order it is shown
STEERS = (
    ("like_games", "More like"),
    ("boost_tags", "Must have"),
    ("saved_boost_tags", "Boosted"),
    ("avoid_tags", "Avoid"),
    ("player_notes", "Notes"),
)
# pick fields with their own column (or the link); known extra fields get a formatted `Column`, any other field
# becomes a generic column after those
BASE_FIELDS = {"name", "appid", "score", "positive_ratio", "matched_tags", "playtime_hours"}
# inline punctuation only: cells never start a line, so `#`, `-` and `+` are safe there
MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|&~])")


type Pick = Mapping[str, object]


@dataclass(frozen=True)
class Column:
    """Known extra fields shown as one formatted column, like the terminal report. A section only gets the column
    when some pick has a value for it."""

    header: str
    fields: tuple[str, ...]  # the pick fields it renders (they get no generic column)
    text: Callable[[Pick], str | None]  # None: this pick has no value
    blank: str = "-"  # the cell of a pick with no value


def price(pick: Pick) -> str | None:
    return ui.price(pick) if isinstance(pick.get("price_usd"), int | float) else None


def story(pick: Pick) -> str | None:
    hours = pick.get("main_story_hours")
    if isinstance(hours, int | float):
        return f"{hours}h"
    return "?" if pick.get("length_unknown") else None


def wishlist(pick: Pick) -> str | None:
    return "★" if pick.get("on_wishlist") is True else None


def via_tag(pick: Pick) -> str | None:
    tag = pick.get("via_tag")
    return str(tag) if tag is not None else None


COLUMNS = (
    Column("Price", ("price_usd", "discount_percent"), price),
    Column("Story", ("main_story_hours", "length_unknown"), story),
    Column("Wishlist", ("on_wishlist",), wishlist, blank=""),
    Column("Via tag", ("via_tag",), via_tag),
)
KNOWN_FIELDS = BASE_FIELDS.union(*(column.fields for column in COLUMNS))


@dataclass(frozen=True)
class Section:
    """One table: a heading, the extra column (liked % or hours) and the picks."""

    title: str
    picks: Sequence[Pick]
    extra_header: str
    extra: str

    @property
    def columns(self) -> list[Column]:
        """Known extra columns some pick has a value for (no Price column for owned backlog games)."""
        return [column for column in COLUMNS if any(column.text(pick) is not None for pick in self.picks)]

    @property
    def more(self) -> list[str]:
        """Fields other tasks add that have no `Column`, in first-seen order. A field that is None on every pick gets
        no column: it would only hold dashes."""
        return list(
            dict.fromkeys(
                key
                for pick in self.picks
                for key, value in pick.items()
                if key not in KNOWN_FIELDS and value is not None
            )
        )

    def headers(self) -> list[str]:
        known = [column.header for column in self.columns]
        return ["#", "Game", "Fit", self.extra_header, *known, *(header(key) for key in self.more), "Matched tags"]

    def cells(self, pick: Pick) -> list[str]:
        """Plain-text cells after the rank and name (the renderers escape them)."""
        value = pick.get(self.extra)
        if self.extra == "positive_ratio":
            extra = f"{value:.0%}" if isinstance(value, int | float) else "-"
        else:
            extra = f"{value}h" if isinstance(value, int | float) else "-"
        score = pick.get("score")
        fit = f"{score:.2f}" if isinstance(score, int | float) else "-"
        known = [text if (text := column.text(pick)) is not None else column.blank for column in self.columns]
        return [fit, extra, *known, *(cell(pick.get(key)) for key in self.more), cell(pick.get("matched_tags"))]


def header(key: str) -> str:
    return key.replace("_", " ").capitalize()


def cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def store_url(pick: Pick) -> str | None:
    appid = pick.get("appid")
    return STORE_URL.format(appid=appid) if isinstance(appid, int) and not isinstance(appid, bool) else None


def sections(result: Mapping[str, object]) -> list[Section]:
    found: list[Section] = []
    for key, title, extra_header, extra in (
        ("recommendations", "Recommended for you", "Liked", "positive_ratio"),
        ("backlog", "From your backlog", "Hours", "playtime_hours"),
        ("wildcards", "Wildcards: outside your usual tags", "Liked", "positive_ratio"),
    ):
        picks = result.get(key)
        if isinstance(picks, list) and picks:
            found.append(Section(title, [pick for pick in picks if isinstance(pick, Mapping)], extra_header, extra))
    return found


def steers(result: Mapping[str, object]) -> list[tuple[str, str]]:
    """(label, values) for every steer that was applied."""
    applied = result.get("applied")
    if not isinstance(applied, Mapping):
        return []
    found: list[tuple[str, str]] = []
    for key, label in STEERS:
        values = applied.get(key)
        if isinstance(values, list) and values:
            found.append((label, cell(values)))
    return found


def title(day: date) -> str:
    return f"{TITLE}, {day.isoformat()}"


# ---------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------
def md_escape(text: str) -> str:
    """Backslash-escape Markdown punctuation and flatten newlines, so a value can't break a table row."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def md_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def md_table(section: Section) -> list[str]:
    headers = section.headers()
    lines = [f"## {section.title}", "", md_row(headers), md_row(["---"] * len(headers))]
    for rank, pick in enumerate(section.picks, start=1):
        name = md_escape(str(pick.get("name", "?")))
        url = store_url(pick)
        linked = f"[{name}]({url})" if url else name
        lines.append(md_row([str(rank), linked, *(md_escape(text) for text in section.cells(pick))]))
    return [*lines, ""]


def to_markdown(result: Mapping[str, object], day: date | None = None) -> str:
    """The result as Markdown: title with the date, what steered it, then recommendations, backlog and wildcards."""
    lines = [f"# {title(day or date.today())}", ""]
    applied = steers(result)
    if applied:
        lines += [f"- **{label}:** {md_escape(values)}" for label, values in applied] + [""]
    for section in sections(result):
        lines += md_table(section)
    return "\n".join(lines)


# ---------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------
CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #1f2328; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.2rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #d0d7de; padding: 0.35rem 0.6rem; text-align: left; vertical-align: top; }
th { background: #f6f8fa; } td.num { text-align: right; white-space: nowrap; }
a { color: #0969da; } ul.steers { padding-left: 1.2rem; }
""".strip()
NUMERIC_COLUMNS = 3  # rank, then (after the name) fit and the extra column are right-aligned


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def html_table(section: Section) -> list[str]:
    lines = [f"<h2>{esc(section.title)}</h2>", "<table>", "<thead><tr>"]
    lines += [f"<th>{esc(text)}</th>" for text in section.headers()]
    lines += ["</tr></thead>", "<tbody>"]
    for rank, pick in enumerate(section.picks, start=1):
        name = esc(str(pick.get("name", "?")))
        url = store_url(pick)
        linked = f'<a href="{esc(url)}">{name}</a>' if url else name
        cells = section.cells(pick)
        numeric = [f'<td class="num">{esc(text)}</td>' for text in cells[: NUMERIC_COLUMNS - 1]]
        rest = [f"<td>{esc(text)}</td>" for text in cells[NUMERIC_COLUMNS - 1 :]]
        lines.append(f'<tr><td class="num">{rank}</td><td>{linked}</td>{"".join(numeric + rest)}</tr>')
    return [*lines, "</tbody>", "</table>"]


def to_html(result: Mapping[str, object], day: date | None = None) -> str:
    """The result as one self-contained HTML page: inline CSS, no scripts, store links on every name."""
    heading = esc(title(day or date.today()))
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{heading}</title>",
        f"<style>\n{CSS}\n</style>",
        "</head>",
        "<body>",
        f"<h1>{heading}</h1>",
    ]
    applied = steers(result)
    if applied:
        lines += ['<ul class="steers">']
        lines += [f"<li><strong>{esc(label)}:</strong> {esc(values)}</li>" for label, values in applied]
        lines += ["</ul>"]
    for section in sections(result):
        lines += html_table(section)
    return "\n".join([*lines, "</body>", "</html>", ""])


# file extension -> renderer
FORMATS: dict[str, Callable[[Mapping[str, object]], str]] = {".md": to_markdown, ".html": to_html}
