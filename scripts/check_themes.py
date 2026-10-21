"""Every theme in ui/themes passes a contrast check, and defines every colour.

    python scripts/check_themes.py

RELEASE-PLAN.md decision 7: themes are colours only, so a theme can break
nothing but legibility, and that is what this checks. Each pair below is text
the dashboard draws on a ground (ui/bench.css), held to WCAG AA: 4.5 for body
text, 3.0 for large or bold text and for non-text marks. Grounds that bench.css
mixes from these tokens (the chip grounds) are left to the eye.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
THEMES = ROOT / "ui" / "themes"

# Every colour token a theme must set (fonts, measure and radius come from
# the default theme's file and may be repeated).
REQUIRED = ("ink", "ink-2", "ink-deep", "paper", "paper-2", "band", "signal", "link", "muted",
            "line", "brand", "primary", "primary-deep", "sky", "warn", "err")

# (text, ground, minimum ratio, where it appears)
PAIRS = [
    ("ink", "paper", 4.5, "body text"),
    ("ink", "paper-2", 4.5, "text in panels"),
    ("ink", "band", 4.5, "text on bands and chips"),
    ("muted", "paper", 4.5, "secondary text"),
    ("muted", "paper-2", 4.5, "secondary text in panels"),
    ("link", "paper", 4.5, "links"),
    ("link", "paper-2", 4.5, "links in panels"),
    ("err", "paper-2", 4.5, "error text"),
    ("warn", "paper-2", 4.5, "warning text"),
    ("paper", "ink", 4.5, "primary button"),
    ("on-err", "err", 4.5, "danger button"),
    ("log-ink", "log-ground", 4.5, "onboarding log"),
    ("ink-deep", "sky", 4.5, "'new' chip"),
    ("brand", "paper", 3.0, "meters, step marks, button shadow"),
    # Only for a theme with a floor (--floor, a page ground drawn as a
    # pattern): the header and sidebar text sit on it, held to its darkest board.
    ("ink", "floor-ground", 4.5, "header text on the floor"),
    ("muted", "floor-ground", 4.5, "secondary text on the floor"),
    ("link", "floor-ground", 4.5, "links on the floor"),
]
OPTIONAL = {"floor-ground"}
# What bench.css falls back to when a theme leaves these out.
FALLBACK = {"on-err": "#ffffff", "log-ink": "paper", "log-ground": "ink-deep"}


def tokens(path: pathlib.Path) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", path.read_text(encoding="utf-8"), re.S)
    return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block.group(1))) if block else {}


def colour(theme: dict[str, str], name: str) -> str:
    value = theme.get(name) or FALLBACK.get(name, "")
    return colour(theme, value) if value in theme or value in FALLBACK else value.strip()


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check(name: str, theme: dict[str, str]) -> list[str]:
    problems = [f"{name}: --{t} is not set" for t in REQUIRED if t not in theme]
    for text, ground, need, where in PAIRS:
        if ground in OPTIONAL and ground not in theme:
            continue
        a, b = colour(theme, text), colour(theme, ground)
        if not (re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", a or "")
                and re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", b or "")):
            problems.append(f"{name}: {where}: --{text} or --{ground} is not a hex colour")
            continue
        if (r := ratio(a, b)) < need:
            problems.append(f"{name}: {where}: --{text} on --{ground} is {r:.2f}:1, needs {need}")
    return problems


def main() -> int:
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0 and round(ratio("#777777", "#ffffff"), 2) == 4.48
    files = sorted(THEMES.glob("*.css"))
    problems = [p for f in files for p in check(f.stem, tokens(f))]
    for p in problems:
        print(p)
    print(f"{len(files)} themes: " + (f"{len(problems)} problems" if problems else "all legible"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
