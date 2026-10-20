"""Review tasks from real, recent bug fixes, each paired with a control.

    python suite/rebench.py --count 5                     # five fixes -> five bug + five control drafts
    python suite/rebench.py --since 2026-03-01 --dry-run  # what would be written
    python suite/rebench.py --count 5 --offset 200        # further down the list

Source: nebius/SWE-rebench-V2 on Hugging Face (CC-BY-4.0), read through the
datasets-server HTTP API, no `datasets` package. Each row is a merged pull
request with its fix as a diff and the commit it was made against. See
suite/REBENCH.md for why, and for what is kept.

A kept fix changes one Python file in one small contiguous hunk (at most
five lines removed and five added, not only comments or blank lines). The bug
task is that file at the base commit, the line numbers as in the file, trimmed
to 400 lines around the change if longer; the answer is any line the fix
removed, or either side of the gap where it only added lines. The control is
the same lines after the fix, where the answer is NO FAULT. Both are drafts
until someone has read the fix and agreed it is the bug.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
REJECTED = ROOT / "suite" / "rebench-rejected.json"     # instance id -> why it was dropped in review
API ="https://datasets-server.huggingface.co"
DATASET = "nebius/SWE-rebench-V2"
RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
CACHE = DATA / "runs" / "cache"
FIELDS = ("instance_id", "repo", "base_commit", "created_at", "license", "patch", "problem_statement")
MAX_REMOVED = MAX_ADDED = 5
MAX_LINES = 400
PAGE = 50
PROMPT = ("The Python file below is from a public project. Either it contains a bug that a later"
          " commit to the project fixed, or it is the version after that fix. Lines are numbered"
          " as in the file{excerpt}.\n\n"
          "Your first line must be exactly `FAULT: line <n>`, naming a line of the bug (if the fix"
          " only added code, the line just before or after where it belongs), or `NO FAULT` if"
          " this is the fixed version. Then explain in at most three sentences. Plain ASCII.\n\n{code}")


def api(path: str, **query) -> dict:
    """datasets-server JSON. Its filter index loads on demand and answers 500
    ("index is loading", "Unexpected error") for minutes at a time, so a 500 is
    retried for up to five minutes."""
    url = f"{API}/{path}?" + urllib.parse.urlencode(query)
    for attempt in range(16):
        try:
            return json.loads(_get(url))
        except urllib.error.HTTPError as exc:
            if exc.code != 500 or attempt == 15:
                raise
            if attempt == 0:
                print("  datasets-server is busy (500); retrying for up to five minutes", flush=True)
            time.sleep(20)
    raise AssertionError("unreachable")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "benchwarmers"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def rows(since: str, need: int) -> list[dict]:
    """Up to `need` Python rows created on or after `since`, newest first.
    Kept a day in runs/cache/ (gitignored), so a rerun does not depend on the
    API being up."""
    path = CACHE / f"rebench-{since}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - path.stat().st_mtime < 86400 and (cached["complete"] or len(cached["rows"]) >= need):
            return cached["rows"]
    except (OSError, ValueError, KeyError):
        pass
    where = f"\"language\"='python' AND \"created_at\">='{since}'"
    data, complete = [], False
    while len(data) < need:
        page = api("filter", dataset=DATASET, config="default", split="train", where=where,
                   orderby="\"created_at\" DESC", offset=len(data), length=PAGE)["rows"]
        data += [{k: r["row"].get(k) for k in FIELDS} for r in page]
        if len(page) < PAGE:
            complete = True
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"complete": complete, "rows": data}, ensure_ascii=False), encoding="utf-8")
    return data


def single_hunk(patch: str) -> tuple[dict | None, str]:
    """(path, old start, hunk lines as (tag, text)) for a one-file, one-hunk,
    small, contiguous change to a Python file that is not a test; else None
    and why not."""
    if patch.count("\ndiff --git ") + patch.startswith("diff --git ") != 1:
        return None, "changes more than one file"
    m = re.search(r"^\+\+\+ b/(.+)$", patch, re.M)
    if not m or "\n--- /dev/null" in patch:
        return None, "adds or deletes a file"
    path = m.group(1).strip()
    if not path.endswith(".py") or re.search(r"(^|/)(tests?|testing)(/|_)|test_[^/]*\.py$|_test\.py$", path):
        return None, "not a Python source file"
    heads = re.findall(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", patch, re.M)
    if len(heads) != 1:
        return None, "more than one hunk" if heads else "no hunk"
    body = patch.split("\n@@", 1)[1].split("\n", 1)[1].rstrip("\n")
    # A blank context line is " ", or "" where something stripped trailing spaces.
    lines = [(ln[0], ln[1:].rstrip("\r")) if ln else (" ", "") for ln in body.splitlines()
             if ln[:1] in (" ", "-", "+", "")]
    changed = [i for i, (tag, _) in enumerate(lines) if tag != " "]
    if not changed or changed != list(range(changed[0], changed[-1] + 1)):
        return None, "changes are not one contiguous block"
    removed = [t for tag, t in lines if tag == "-"]
    added = [t for tag, t in lines if tag == "+"]
    if len(removed) > MAX_REMOVED or len(added) > MAX_ADDED:
        return None, f"too large ({len(removed)} removed, {len(added)} added)"
    if _code(removed) == _code(added):
        return None, "only comments or whitespace"
    return {"path": path, "start": int(heads[0]), "lines": lines}, ""


def _code(lines: list[str]) -> list[str]:
    """Lines without comments, blank lines or spacing inside a line: what the
    interpreter sees, roughly. Indentation is kept, because in Python moving a
    line in or out of a block is a code change."""
    out = []
    for ln in lines:
        code = re.sub(r"#.*", "", ln)
        if code.strip():
            out.append(code[:len(code) - len(code.lstrip())] + re.sub(r"\s+", "", code))
    return out


def apply_fix(before: list[str], hunk: dict) -> tuple[list[str], list[int]] | None:
    """(file after the fix, line numbers of the bug in `before`), or None if the
    hunk does not match the file."""
    start, lines = hunk["start"], hunk["lines"]
    old = [t for tag, t in lines if tag != "+"]
    # rstrip: a diff may have lost a blank line's trailing spaces.
    if [t.rstrip() for t in before[start - 1:start - 1 + len(old)]] != [t.rstrip() for t in old]:
        return None
    after = before[:start - 1] + [t for tag, t in lines if tag != "-"] + before[start - 1 + len(old):]
    keys, n, gap = [], start, None
    for tag, _ in lines:
        if tag == "-":
            keys.append(n)
        if tag == "+" and gap is None:
            gap = n                          # added before old line n
        if tag != "+":
            n += 1
    if not keys:                             # only added: either side of the gap
        keys = [k for k in (gap - 1, gap) if 1 <= k <= len(before)]
    return after, keys


def window(length: int, first: int, last: int) -> tuple[int, int]:
    """1-based inclusive lines to show: all of it, or MAX_LINES around the change."""
    if length <= MAX_LINES:
        return 1, length
    lo = max(1, (first + last) // 2 - MAX_LINES // 2)
    hi = min(length, lo + MAX_LINES - 1)
    return max(1, hi - MAX_LINES + 1), hi


def numbered(lines: list[str], lo: int, hi: int) -> str:
    width = len(str(hi))
    return "\n".join(f"{i:>{width}}| {lines[i - 1]}" for i in range(lo, hi + 1))


def build(row: dict) -> tuple[list[dict] | None, str]:
    """The bug and control specs for one dataset row, or None and why not."""
    hunk, why_not = single_hunk(row.get("patch") or "")
    if hunk is None:
        return None, why_not
    repo, commit = row["repo"], row["base_commit"]
    try:
        text = _get(RAW.format(repo=repo, commit=commit, path=urllib.parse.quote(hunk["path"])))
    except urllib.error.URLError as exc:
        return None, f"could not fetch the file ({exc})"
    before = text.splitlines()
    fixed = apply_fix(before, hunk)
    if fixed is None:
        return None, "the diff does not match the file at its base commit"
    after, keys = fixed
    if not keys:
        return None, "no line to name (an addition to an empty file)"
    delta = len(after) - len(before)
    lo, hi = window(len(before), min(keys), max(keys))
    hi_after = min(len(after), hi + delta)
    excerpt = "" if (lo, hi) == (1, len(before)) else f" (an excerpt: lines {lo}-{hi})"
    number = row["instance_id"].rsplit("-", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", repo.split("/")[-1].lower()).strip("-")[:24]
    task = f"rebench-{slug}-{number}"
    url = f"https://github.com/{repo}/pull/{number}"
    removed = [t.strip() for tag, t in hunk["lines"] if tag == "-"]
    added = [t.strip() for tag, t in hunk["lines"] if tag == "+"]
    common = {"draft": True, "shape": "rebench", "source": row["instance_id"], "upstream": repo,
              "source_url": url, "created": row.get("created_at", ""), "license": row.get("license", ""),
              "score_in": "first_line", "min_answer_chars": 8}
    why = (f"{repo} PR #{number}, created {row.get('created_at', '?')}: {url}\n"
           f"The fix in {hunk['path']} (base {commit[:10]}) changed line(s) {', '.join(map(str, keys))}:\n"
           + "".join(f"  - {t}\n" for t in removed) + "".join(f"  + {t}\n" for t in added)
           + f"Problem: {' '.join((row.get('problem_statement') or '').split())[:300]}\n"
           "Check the fix is the bug, and visible from this file alone, before removing draft.")
    bug = {**common, "id": f"{task}-bug", "title": "Real bug: name the line a later fix changed",
           "why": why, "prompt": PROMPT.format(excerpt=excerpt, code=numbered(before, lo, hi)),
           "must_find": [{"regex": rf"^\W*fault:\s*line\s*(?:{'|'.join(map(str, keys))})\b",
                          "label": f"fault: line {' or '.join(map(str, keys))}"}],
           "must_not_claim": ["no fault"]}
    excerpt_after = "" if (lo, hi_after) == (1, len(after)) else f" (an excerpt: lines {lo}-{hi_after})"
    control = {**common, "id": f"{task}-control", "title": "Real-bug control: say NO FAULT",
               "why": f"{hunk['path']} after the fix in {url}; the paired bug task is the file before it."
                      " The answer is NO FAULT.",
               "prompt": PROMPT.format(excerpt=excerpt_after, code=numbered(after, lo, hi_after)),
               "must_find": [{"any_of": ["no fault"]}],
               "must_not_claim": [{"regex": r"^\W*fault:\s*line", "label": "fault: line"}]}
    return [bug, control], ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=5, help="bug/control pairs to write")
    # The dataset (checked 2026-10-17) ends 2025-10-31; the newest months are the
    # least likely to be in a model's training data, not guaranteed absent.
    ap.add_argument("--since", default="2025-06-01", help="only fixes created on or after this date")
    ap.add_argument("--offset", type=int, default=0, help="skip this many matching rows first")
    ap.add_argument("--scan", type=int, default=400, help="give up after reading this many rows")
    ap.add_argument("--dry-run", action="store_true", help="fetch and check, write nothing")
    args = ap.parse_args()
    taken = {json.loads(p.read_text(encoding="utf-8")).get("source", "")
             for p in TASKS.glob("rebench-*/task.json")}
    taken |= set(json.loads(REJECTED.read_text(encoding="utf-8")) if REJECTED.exists() else {})
    made = seen = 0
    skipped: dict[str, int] = {}
    for row in rows(args.since, args.offset + args.scan)[args.offset:]:
        if made >= args.count:
            break
        seen += 1
        if row["instance_id"] in taken:
            continue
        specs, why_not = build(row)
        if specs is None:
            reason = re.sub(r"\(.*", "", why_not).strip()
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        made += 1
        print(f"  {row['instance_id']} ({row.get('created_at', '')[:10]}): {specs[0]['must_find'][0]['label']}"
              f"  ->  {specs[0]['id']} / -control" + ("  [dry run]" if args.dry_run else ""))
        if not args.dry_run:
            for spec in specs:
                d = TASKS / spec["id"]
                d.mkdir(parents=True, exist_ok=True)
                (d / "task.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                                             encoding="utf-8")
    print(f"{made} pair(s) from {seen} row(s) read; skipped: "
          + (", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])) or "none"))
    if made and not args.dry_run:
        print("drafts written; read each bug task's `why` and the fix, then remove \"draft\": true")


if __name__ == "__main__":
    main()
