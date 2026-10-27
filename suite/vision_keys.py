"""How every vision task asks and is scored: answers as `key: value` lines,
a key per field, `not shown` for what the picture does not show.

Shared by suite/vision_tasks.py (the made pictures), suite/vision_sample.py
(items from public datasets) and any task written by hand, so one answer
style is scored one way. Standard library only.
"""

from __future__ import annotations

import re

NOT_SHOWN = (r"(not shown|not visible|not present|not listed|not given|not stated|not on the|not in the"
             r"|not provided|not included|none shown|n/?a\b|none\b|no such|does not show|doesn't show"
             r"|cannot be (?:determined|answered|seen)|can't be (?:determined|answered|seen)|unanswerable)")
PROMPT = ("Read the attached image and answer in exactly these lines, one per line, nothing else:\n\n{lines}\n\n"
          "If the image does not show something, write `not shown` for it. Do not guess.")


def key(name: str, value: str) -> str:
    """A regex for the line `name: value`, markdown around the key allowed."""
    k = re.escape(name).replace("_", "[ _]")
    return rf"(?m)^[^\w\n]*{k}[^\w\n]*:[ \t*_`\"']*{value}"


def clause(name: str, value: str, label: str) -> dict:
    return {"regex": key(name, value), "label": label}


def not_shown(name: str) -> tuple[dict, dict]:
    """(must_find, must_not_claim) for a field the picture does not answer:
    `not shown` is right, any other value an invention."""
    return (clause(name, NOT_SHOWN, f"{name}: not shown"),
            clause(name, rf"(?!{NOT_SHOWN}|unknown|unclear)\w", f"{name}: a value (the picture does not answer it)"))


def number(text: str) -> str:
    """A regex for a printed amount however its thousands are separated:
    "45,500" also matches 45.500, 45 500 and 45500; a currency word or sign
    before it is allowed."""
    digits = re.sub(r"[^\d.,]", "", text)
    m = re.fullmatch(r"(.*?)[.,](\d{1,2})", digits)              # one or two digits after the last mark: cents
    whole, cents = (m.group(1), m.group(2)) if m else (digits, None)
    groups = [g for g in re.split(r"[.,]", whole) if g]
    body = r"[.,\s]?".join(groups)
    if cents:
        body += rf"[.,]{cents}"
    return rf"(?:[a-z]{{1,3}}\.?\s*|[$€£¥]\s*)?{body}(?!\d)"


def phrase(text: str) -> str:
    """A regex for a short answer anywhere on its line, spacing and case loose."""
    words = [re.escape(w) for w in text.lower().split()]
    return r".*" + r"\s+".join(words)


def _selftest() -> None:
    n = number("45,500")
    for ok in ("45,500", "45.500", "45 500", "45500", "rp 45.500", "Rp. 45,500"):
        assert re.fullmatch(n, ok.lower()), (ok, n)
    for bad in ("45,5000", "455,00"):
        assert not re.fullmatch(n, bad), (bad, n)
    assert re.fullmatch(number("3.14%").removesuffix("(?!\\d)"), "3.14"), number("3.14%")
    assert re.fullmatch(number("$1,284.50"), "eur 1,284.50") and re.fullmatch(number("23.000"), "23000")
    find, claim = not_shown("q3")
    # Answers arrive lowercased (run.score).
    assert re.search(find["regex"], "q3: not shown") and not re.search(claim["regex"], "q3: not shown")
    assert re.search(claim["regex"], "q3: 12 questions") and re.search(find["regex"], "**q3**: cannot be determined")
    assert re.search(key("q1", phrase("Unauthorized transactions")), "q1: from unauthorized  transactions.")


if __name__ == "__main__":
    _selftest()
    print("ok")
