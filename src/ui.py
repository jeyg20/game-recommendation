"""Terminal output: colors, Markdown rendering, result tables and a status line (spinner + progress). Stdlib only.

Colors are off when stdout isn't a terminal or NO_COLOR is set, and the status line is only drawn in a terminal,
so piped output and logs stay plain text.
"""

import itertools
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

INTERACTIVE = sys.stdout.isatty()
COLOR = INTERACTIVE and "NO_COLOR" not in os.environ

CODES = {"bold": "1", "dim": "2", "italic": "3", "red": "31", "green": "32", "yellow": "33", "cyan": "36", "grey": "90"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def style(text: str, *names: str) -> str:
    if not COLOR or not names:
        return text
    return f"\x1b[{';'.join(CODES[name] for name in names)}m{text}\x1b[0m"


def visible_len(text: str) -> int:
    return len(ANSI.sub("", text))


def width() -> int:
    return min(shutil.get_terminal_size((100, 24)).columns, 120)


def truncate(text: str, size: int) -> str:
    """Cut plain text to `size` columns with an ellipsis."""
    if size <= 0:
        return ""
    return text if len(text) <= size else text[: size - 1] + "…"


def pad(text: str, size: int) -> str:
    """Left-align styled text in `size` columns."""
    return text + " " * max(size - visible_len(text), 0)


# ---------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------
# Inline spans use their own "off" codes (not a full reset) so they can sit inside a colored line.
INLINE = [
    (re.compile(r"`([^`]+)`"), "\x1b[36m\\1\x1b[39m"),
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__"), lambda m: f"\x1b[1m{m[1] or m[2]}\x1b[22m"),
    (
        re.compile(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)"),
        lambda m: f"\x1b[3m{m[1] or m[2]}\x1b[23m",
    ),
]


def inline(text: str) -> str:
    """Style inline Markdown spans; without color, just drop the markers so plain text reads cleanly."""
    for pattern, replacement in INLINE:
        text = pattern.sub(replacement if COLOR else lambda m: next(group for group in m.groups() if group), text)
    return text


def wrap(text: str, first_prefix: str, rest_prefix: str, size: int) -> list[str]:
    """Greedy word wrap that measures visible width, so escape codes don't count. Styles carry across lines."""
    lines: list[str] = []
    line, line_len = first_prefix, visible_len(first_prefix)
    for word in text.split(" "):
        word_len = visible_len(word)
        if line_len + word_len > size and line.strip():
            lines.append(line.rstrip())
            line, line_len = rest_prefix, visible_len(rest_prefix)
        line += word + " "
        line_len += word_len + 1
    lines.append(line.rstrip())
    return lines


def render_markdown(text: str) -> str:
    """The subset Gemini uses: headings, bullets, numbered lists, bold, italics, code, rules."""
    size = width() - 2
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(style(f"  {line}", "grey"))
            continue
        if heading := re.match(r"^#{1,6}\s+(.*)", line):
            out.extend(["", style(inline(heading[1]), "bold", "cyan")])
        elif re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            out.append(style("─" * min(size, 60), "grey"))
        elif bullet := re.match(r"^(\s*)[-*+]\s+(.*)", line):
            indent = "  " + bullet[1]
            out.extend(wrap(inline(bullet[2]), f"{indent}{style('•', 'cyan')} ", indent + "  ", size))
        elif numbered := re.match(r"^(\s*)(\d+)[.)]\s+(.*)", line):
            indent = "  " + numbered[1]
            marker = f"{numbered[2]}."
            out.extend(
                wrap(inline(numbered[3]), f"{indent}{style(marker, 'cyan')} ", indent + " " * (len(marker) + 1), size)
            )
        elif line.strip():
            out.extend(wrap(inline(line.strip()), "", "", size))
        else:
            out.append("")
    return "\n".join(out).strip("\n")


# ---------------------------------------------------------------------
# Result tables
# ---------------------------------------------------------------------
BAR_CELLS = 8
PARTIAL_BLOCKS = "▏▎▍▌▋▊▉"


def bar(score: float) -> str:
    """A fit score in [0, 1] as a block bar, colored by how good it is."""
    eighths = round(max(0.0, min(score, 1.0)) * BAR_CELLS * 8)
    full, part = divmod(eighths, 8)
    blocks = "█" * full + (PARTIAL_BLOCKS[part - 1] if part else "")
    color = "green" if score >= 0.6 else "yellow" if score >= 0.4 else "grey"
    return style(blocks.ljust(BAR_CELLS), color) + f" {score:.2f}"


def table(title: str, picks: Sequence[Mapping[str, object]], extra_header: str, extra: str) -> str:
    """One pick per row: rank, name, fit bar, an extra column (rating or hours), matched tags."""
    if not picks:
        return ""
    size = width()
    name_size = min(max(len(str(pick["name"])) for pick in picks), 34)
    fixed = 2 + 3 + 2 + name_size + 2 + BAR_CELLS + 5 + 2 + 5 + 2
    why_size = max(size - fixed, 12)

    rows = [
        "",
        style(title, "bold", "cyan"),
        style(f"  {'#':>3}  {'Game':<{name_size}}  {'Fit':<{BAR_CELLS + 5}}  {extra_header:>5}  Why", "grey"),
    ]
    for rank, pick in enumerate(picks, start=1):
        value = pick.get(extra)
        if extra == "positive_ratio":
            cell = f"{value:.0%}" if isinstance(value, float) else "-"
        else:
            cell = f"{value}h" if value is not None else "-"
        matched = pick.get("matched_tags")
        tags = ", ".join(str(tag) for tag in matched) if isinstance(matched, list) else ""
        score = pick.get("score")
        fit = bar(float(score)) if isinstance(score, int | float) else "-"
        name = pad(style(truncate(str(pick["name"]), name_size), "bold"), name_size)
        rows.append(
            f"  {style(f'{rank:>3}', 'grey')}  {name}  {fit}  {cell:>5}  {style(truncate(tags, why_size), 'grey')}"
        )
    return "\n".join(rows)


def steer_summary(applied: Mapping[str, object]) -> str:
    """What steered a recommendation, as one dim line (empty when nothing did)."""
    parts: list[str] = []
    for key, label in (
        ("like_games", "like"),
        ("boost_tags", "must have"),
        ("avoid_tags", "avoid"),
        ("player_notes", "note"),
    ):
        values = applied.get(key)
        if isinstance(values, list) and values:
            parts.append(f"{label}: {', '.join(str(value) for value in values)}")
    return style("  steered by  " + "  ·  ".join(parts), "grey") if parts else ""


def recommendation_tables(result: Mapping[str, object]) -> str:
    """The recommend_games result as tables, straight from the ranking data."""
    picks = result.get("recommendations")
    backlog = result.get("backlog")
    applied = result.get("applied")
    blocks = [
        table("Recommended for you", picks, "Liked", "positive_ratio") if isinstance(picks, list) else "",
        steer_summary(applied) if isinstance(applied, Mapping) else "",
        table("From your backlog", backlog, "Hours", "playtime_hours") if isinstance(backlog, list) else "",
    ]
    return "\n".join(block for block in blocks if block)


# ---------------------------------------------------------------------
# Status line
# ---------------------------------------------------------------------
class Status:
    """One self-redrawing line at the bottom of the output: spinner, label, optional progress bar, elapsed time.

    Long-running code reports through `update` and prints through `say`, so messages never collide with the line.
    Outside a terminal nothing is drawn and `say` is a plain print.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.label = ""
        self.progress: tuple[int, int] | None = None
        self.started = 0.0
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()

    @contextmanager
    def running(self, label: str) -> Iterator[None]:
        """Show the line for the duration of the block. Nested blocks just swap the label and restore it."""
        if self.thread is not None:
            previous = (self.label, self.progress)
            self.update(label)
            try:
                yield
            finally:
                self.label, self.progress = previous
            return

        self.label, self.progress, self.started = label, None, time.monotonic()
        if INTERACTIVE:
            self.stopping.clear()
            self.thread = threading.Thread(target=self.spin, daemon=True)
            self.thread.start()
        try:
            yield
        finally:
            if self.thread is not None:
                self.stopping.set()
                self.thread.join()
                self.thread = None
                with self.lock:
                    self.clear()

    def update(self, label: str | None = None, done: int | None = None, total: int | None = None) -> None:
        with self.lock:
            if label is not None:
                self.label = label
                self.progress = None
            if done is not None and total:
                self.progress = (done, total)

    def say(self, message: str) -> None:
        """Print a line above the status line."""
        with self.lock:
            if self.thread is not None:
                self.clear()
            print(message, flush=True)

    def clear(self) -> None:
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()

    def spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self.stopping.wait(0.08):
                return
            with self.lock:
                self.draw(frame)

    def draw(self, frame: str) -> None:
        elapsed = f"{time.monotonic() - self.started:.0f}s"
        progress = ""
        if self.progress is not None:
            done, total = self.progress
            cells = 14
            filled = round(cells * done / total)
            progress = f"  ▕{'█' * filled}{'░' * (cells - filled)}▏ {done}/{total}"
        text = truncate(f"{self.label}{progress}  {elapsed}", width() - 6)
        sys.stdout.write(f"\r\x1b[K  {style(frame, 'cyan')} {style(text, 'grey')}")
        sys.stdout.flush()


status = Status()
