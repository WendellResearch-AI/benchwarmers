"""Generate seeded-bug tasks from real private code, each paired with a control.

    python suite/seed.py --count 6                # six functions -> six bug + six control drafts
    python suite/seed.py --count 6 --seed 42      # reproducible
    python suite/seed.py --repo myapp --count 4   # a key in config/repos.json
    python suite/seed.py --hard --count 4 --kinds args,off-by-one,remove-call

WHY
---
Review is the work the bench hands out most, and hand-built review tasks are
slow to make: every answer key had to be found and verified first. A seeded
bug has its answer key by construction. Take a real function from a private
repository — so no model has seen it — change ONE thing so it no longer does
what its author intended, and ask which line changed. Fresh variants are
unlimited.

EVERY BUG HAS A CONTROL
-----------------------
The same function, untouched, where the right answer is NO FAULT. Without it a
model that always names a line would look like a good reviewer; with it,
claiming a fault in clean code is scored as invention, which is the failure
that costs the most.

WHAT IS CHANGED
---------------
Ten kinds (--kinds picks some; the run's kind is chosen first, at random, so
common ones do not crowd out rare ones):
  compare        < <= > >= == != flipped to its neighbour
  boolop         and <-> or
  return-bool    a returned True/False
  not            a `not` removed after if/elif/while
  number         a small int (-2..2) moved by one or set to 0
  args           two adjacent positional arguments swapped
  off-by-one     range(n) -> range(n - 1) or range(1, n); x[i:] -> x[i + 1:]; x[:j] -> x[:j - 1]
  break-continue break <-> continue
  remove-call    a call statement deleted (`pass` where the block would be empty)
  augassign      += <-> -=, *= -> /=
The first four are single tokens found with `tokenize`; the rest (ideas from
the operator lists of mutmut and cosmic-ray, no code taken) are found with
`ast`. Every edit replaces text on one line, or deletes one line, so the
author's formatting survives: no style tell, the leak the panel warned about.
Without --kinds, function mode uses the four token kinds (its prompt names
them) and --hard uses all ten.

Changes that rarely alter behaviour are not offered: code in an
`if __name__ == "__main__":` block or after a return/raise/break/continue,
keyword-argument and default values, calls to print/logging/sleep, and
argument swaps for max/min/gcd/lcm/hypot.

The prompt numbers the lines, because a model that miscounts has not reviewed
badly, and asks for `FAULT: line <n>` or `NO FAULT` on the first line, scored
by exact regex (a deleted line accepts the line before or after the gap).
Seeded and control tasks are `"shape": "seeded"` and stay out of the routing
recall for now.

DRAFTS
------
Written as drafts. A change can still leave behaviour unchanged (a branch
that is unreachable, a comparison of equal values), which would make the key
wrong; read each bug task's `why` before removing "draft": true.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import pathlib
import random
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
TASKS = DATA / "suite" / "tasks"
sys.path.insert(0, str(ROOT / "suite"))
from run import DEFAULT_REPO, REPOS  # noqa: E402

FLIP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==",
        "and": "or", "or": "and", "True": "False", "False": "True"}
SKIP_DIRS = ("test", "migrations", "vendor", "__pycache__", ".venv", "build", "dist")
# Each kind as the function prompt names it (--hard names none).
KINDS = {
    "compare": "a comparison operator",
    "boolop": "`and`/`or`",
    "return-bool": "a returned True/False",
    "not": "a removed `not`",
    "number": "a small number",
    "args": "two adjacent arguments swapped",
    "off-by-one": "an off-by-one in a range() or slice",
    "break-continue": "`break` and `continue` swapped",
    "remove-call": "a removed call",
    "augassign": "an augmented assignment operator",
}
TOKEN_KINDS = ("compare", "boolop", "return-bool", "not")
SYMMETRIC = {"max", "min", "gcd", "lcm", "hypot"}
QUIET = re.compile(r"(^|\.)(print|echo|write|log|debug|info|warn|warning|error|exception|critical|sleep)$")
AUG = {"+=": "-=", "-=": "+=", "*=": "/="}
AUG_OP = {"Add": "+=", "Sub": "-=", "Mult": "*="}
# An expression that `x - 1` would not simply shift by one.
LOOSE = (ast.IfExp, ast.Lambda, ast.BoolOp, ast.Compare, ast.NamedExpr, ast.Starred)
# --hard: the whole file, and no hint at what kind of change to look for. The
# first batch (2026-10-17) saturated: three of four models found 6/6 seeded
# bugs and passed 6/6 controls with the function alone and the hint.
HARD_PROMPT = ("The Python file below is either exactly as its author wrote it, or {change}"
               " so that the code no longer does what the author intended."
               " Lines are numbered.\n\n"
               "Your first line must be exactly `FAULT: line <n>`, naming the changed line{gap}, or"
               " `NO FAULT` if nothing was changed. Then explain in at most three sentences."
               " Plain ASCII.\n\n{code}")
PROMPT = ("The Python function below is either exactly as its author wrote it, or {change}"
          " so that it no longer does what the author intended ({hint}). Lines are numbered.\n\n"
          "Your first line must be exactly `FAULT: line <n>`, naming the changed line{gap}, or `NO FAULT`"
          " if nothing was changed. Then explain in at most three sentences. Plain ASCII.\n\n"
          "{code}")


def prompt_for(hard: bool, kinds: list[str], code: str) -> str:
    """One wording per run, never per task, so the prompt cannot give away a
    task's kind. With the four token kinds it is the wording of the first batches."""
    if set(kinds) <= set(TOKEN_KINDS):
        change = "ONE token in it has been changed"
    elif "remove-call" in kinds:
        change = "ONE small change has been made to it (one line edited, or one line removed)"
    else:
        change = "ONE small change has been made to one line"
    gap = " (for a removed line, the line just before or after the gap)" if "remove-call" in kinds else ""
    if hard:
        return HARD_PROMPT.format(change=change, gap=gap, code=code)
    hints = [KINDS[k] for k in kinds]
    hint = hints[0] if len(hints) == 1 else ", ".join(hints[:-1]) + ", or " + hints[-1]
    return PROMPT.format(change=change, hint=hint, gap=gap, code=code)


def files(repo: pathlib.Path):
    """(relative path, "file", every line) for --hard: whole files of 100-400 lines."""
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo)
        if any(part.startswith(SKIP_DIRS) for part in rel.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        if 100 <= len(lines) <= 400:
            yield str(rel).replace("\\", "/"), "file", lines


def functions(repo: pathlib.Path):
    """(relative path, name, source lines) for every function of 12-60 lines."""
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo)
        if any(part.startswith(SKIP_DIRS) for part in rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
                body = lines[start:node.end_lineno]
                if 12 <= len(body) <= 60:
                    yield str(rel).replace("\\", "/"), node.name, body


def candidates(body: list[str], kinds=tuple(KINDS)) -> list[tuple[str, int, int, str, str | None]]:
    """(kind, line index, column, old text, new text) for each possible change;
    new text None deletes the line."""
    src_lines = _dedent(body)
    src = "\n".join(src_lines) + "\n"
    try:
        tree = ast.parse(src)
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (SyntaxError, tokenize.TokenError, IndentationError):
        return []
    indent = _indent(body)
    out = []
    for i, t in enumerate(toks):
        prev = toks[i - 1].string if i else ""
        kind = None
        if t.type == tokenize.OP and t.string in FLIP:
            kind, old, new = "compare", t.string, FLIP[t.string]
        elif t.type == tokenize.NAME and t.string in ("and", "or"):
            kind, old, new = "boolop", t.string, FLIP[t.string]
        elif t.type == tokenize.NAME and t.string in ("True", "False") and prev == "return":
            kind, old, new = "return-bool", t.string, FLIP[t.string]
        elif t.type == tokenize.NAME and t.string == "not" and prev in ("if", "elif", "while"):
            kind, old, new = "not", "not ", ""
        if kind:
            out.append((kind, t.start[0] - 1, t.start[1] + indent, old, new))
    out += [(k, ln, col + indent, old, new) for k, ln, col, old, new in _ast_candidates(tree, src_lines)]
    dead = _dead_lines(tree)
    return [c for c in out if c[0] in kinds and c[1] + 1 not in dead]


def _ast_candidates(tree: ast.AST, lines: list[str]) -> list[tuple[str, int, int, str, str | None]]:
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def seg(n):
        """(line index, start, end, text) of a node that sits on one line."""
        if n.lineno != n.end_lineno:
            return None
        ln = lines[n.lineno - 1]
        a, b = _col(ln, n.col_offset), _col(ln, n.end_col_offset)
        return n.lineno - 1, a, b, ln[a:b]

    def inside(n, kind):
        while n in parent:
            n = parent[n]
            if isinstance(n, kind):
                return True
        return False

    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and type(n.value) is int and abs(n.value) <= 2:
            p = parent.get(n)
            s = seg(n)
            digits = isinstance(p, ast.Call) and ast.unparse(p.func) == "round" and n in p.args[1:]
            if (s and s[3] == str(n.value) and not isinstance(p, (ast.keyword, ast.arguments))
                    and not digits and not inside(n, ast.JoinedStr)):
                for v in sorted({n.value + 1, n.value - 1, 0} - {n.value}):
                    if v > 0 or not isinstance(p, ast.UnaryOp):        # no -0, no --1
                        out.append(("number", s[0], s[1], s[3], str(v)))
        if isinstance(n, ast.Call):
            name = ast.unparse(n.func)
            if name.split(".")[-1] not in SYMMETRIC and not any(isinstance(a, ast.Starred) for a in n.args):
                for x, y in zip(n.args, n.args[1:]):
                    sx, sy = seg(x), seg(y)
                    if sx and sy and sx[0] == sy[0] and sx[3] != sy[3]:
                        ln = lines[sx[0]]
                        out.append(("args", sx[0], sx[1], ln[sx[1]:sy[2]], sy[3] + ln[sx[2]:sy[1]] + sx[3]))
            if name == "range" and len(n.args) == 1 and not n.keywords and not isinstance(n.args[0], LOOSE):
                s = seg(n.args[0])
                if s:
                    out.append(("off-by-one", s[0], s[1], s[3], _shift(n.args[0], s[3], -1)))
                    out.append(("off-by-one", s[0], s[1], s[3], f"1, {s[3]}"))
        if isinstance(n, ast.Slice) and n.step is None and (n.lower is None) != (n.upper is None):
            bound, by = (n.lower, 1) if n.lower is not None else (n.upper, -1)
            s = None if isinstance(bound, LOOSE) else seg(bound)
            if s:
                out.append(("off-by-one", s[0], s[1], s[3], _shift(bound, s[3], by)))
        if isinstance(n, (ast.Break, ast.Continue)):
            s = seg(n)
            if s:
                out.append(("break-continue", s[0], s[1], s[3], "continue" if s[3] == "break" else "break"))
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and not QUIET.search(ast.unparse(n.value.func)):
            s = seg(n)
            if s and lines[s[0]].strip() == s[3]:
                p = parent[n]
                block = next((getattr(p, f) for f in ("body", "orelse", "finalbody")
                              if any(x is n for x in getattr(p, f, []))), [n, n])
                if len(block) == 1:
                    out.append(("remove-call", s[0], s[1], s[3], "pass"))
                else:
                    out.append(("remove-call", s[0], 0, lines[s[0]], None))
        if isinstance(n, ast.AugAssign) and type(n.op).__name__ in AUG_OP \
                and n.target.end_lineno == n.value.lineno == n.lineno:
            ln = lines[n.lineno - 1]
            op = AUG_OP[type(n.op).__name__]
            k = ln.find(op, _col(ln, n.target.end_col_offset), _col(ln, n.value.col_offset))
            if k >= 0:
                out.append(("augassign", n.lineno - 1, k, op, AUG[op]))
    return out


def _shift(node: ast.AST, text: str, by: int) -> str:
    """`text` moved by one: a literal is computed (`v[:0]`, never the tell-tale
    `v[:1 - 1]`), anything else gets `+ 1` / `- 1`."""
    value = node.value if isinstance(node, ast.Constant) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.op, (ast.USub, ast.UAdd)) and type(node.operand.value) is int:
        value = -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
    if type(value) is int:
        return str(value + by)
    return f"{text} {'+' if by > 0 else '-'} 1"


def _dead_lines(tree: ast.AST) -> set[int]:
    """Lines where a change rarely changes behaviour: an `if __name__ ==
    "__main__":` block, and statements after a return/raise/break/continue."""
    dead = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"):
            dead.update(range(n.lineno, n.end_lineno + 1))
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(n, field, None)
            if not isinstance(stmts, list):
                continue
            stop = next((i for i, s in enumerate(stmts)
                         if isinstance(s, (ast.Return, ast.Raise, ast.Break, ast.Continue))), None)
            for s in stmts[stop + 1:] if stop is not None else []:
                dead.update(range(s.lineno, s.end_lineno + 1))
    return dead


def _col(line: str, byte_offset: int) -> int:
    """ast columns count UTF-8 bytes; slicing needs characters."""
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", "ignore"))


def apply(body: list[str], cand: tuple) -> list[str] | None:
    """The changed lines, or None if the text is not where the candidate says."""
    _, line, col, old, new = cand
    if new is None:
        return body[:line] + body[line + 1:]
    if body[line][col:col + len(old)] != old:
        return None
    out = list(body)
    out[line] = out[line][:col] + new + out[line][col + len(old):]
    return out


def answer_lines(cand: tuple, length: int) -> list[int]:
    """Lines a correct answer may name: the changed one, or either side of a deleted one."""
    _, line, _, _, new = cand
    if new is None:
        return [n for n in (line, line + 1) if 1 <= n <= length]
    return [line + 1]


def parses(body: list[str]) -> bool:
    try:
        ast.parse("\n".join(_dedent(body)) + "\n")
        return True
    except SyntaxError:
        return False


def _indent(body: list[str]) -> int:
    return len(body[0]) - len(body[0].lstrip())


def _dedent(body: list[str]) -> list[str]:
    n = _indent(body)
    return [ln[n:] if ln[:n].strip() == "" else ln for ln in body]


def numbered(body: list[str]) -> str:
    width = len(str(len(body)))
    return "\n".join(f"{i:>{width}}| {ln}" for i, ln in enumerate(body, 1))


def write(task_id: str, spec: dict) -> None:
    d = TASKS / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, choices=sorted(REPOS),
                    help="a key in config/repos.json")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--hard", action="store_true",
                    help="whole files of 100-400 lines, and no hint at the kind of change")
    ap.add_argument("--kinds", help=f"comma-separated, from: {', '.join(KINDS)}")
    args = ap.parse_args()
    wanted = args.kinds.split(",") if args.kinds else (list(KINDS) if args.hard else list(TOKEN_KINDS))
    unknown = set(wanted) - set(KINDS)
    if unknown:
        sys.exit(f"unknown kind(s): {', '.join(sorted(unknown))}; choose from {', '.join(KINDS)}")
    kinds = [k for k in KINDS if k in wanted]
    rng = random.Random(args.seed)
    source_of = files if args.hard else functions
    pool = [(rel, name, body, c) for rel, name, body in source_of(REPOS[args.repo])
            if (c := candidates(body, kinds))]
    taken = {json.loads(p.read_text(encoding="utf-8")).get("source", "")
             for p in TASKS.glob("seeded-*/task.json")}
    pool = [p for p in pool if f"{p[0]}::{p[1]}" not in taken]
    if not pool:
        sys.exit("no unused function with a change of these kinds")
    for rel, name, body, cands in rng.sample(pool, min(args.count, len(pool))):
        bugged = cand = None
        while cands and bugged is None:
            kind = rng.choice(sorted({c[0] for c in cands}))      # kind first, so rare kinds get picked
            cand = rng.choice([c for c in cands if c[0] == kind])
            cands.remove(cand)
            b = apply(body, cand)
            bugged = b if b and b != body and parses(b) else None
        if bugged is None:
            continue
        kind, line, _, old, new = cand
        slug = ("hard-" if args.hard else "") + re.sub(r"[^a-z0-9]+", "-", f"{pathlib.Path(rel).stem}-{name}".lower()).strip("-")[:40]
        source = f"{rel}::{name}"
        common = {"draft": True, "shape": "seeded", "repo": args.repo, "source": source,
                  "score_in": "first_line", "min_answer_chars": 8}
        n = line + 1
        keys = answer_lines(cand, len(bugged))
        change = "(line removed)" if new is None else repr(new.strip() or "(removed)")
        write(f"seeded-{slug}-bug", {
            **common, "kind": kind, "id": f"seeded-{slug}-bug", "title": "Seeded bug: name the changed line",
            "why": (f"Seeded in {source}, line {n} ({kind}): {old.strip()!r} -> {change}.\n"
                    f"  before: {body[line].strip()}\n"
                    f"  after:  {'(line removed)' if new is None else bugged[line].strip()}\n"
                    "Check the change alters behaviour before removing draft."),
            "prompt": prompt_for(args.hard, kinds, numbered(bugged)),
            "must_find": [{"regex": rf"^\W*fault:\s*line\s*(?:{'|'.join(map(str, keys))})\b",
                           "label": f"fault: line {' or '.join(map(str, keys))}"}],
            "must_not_claim": ["no fault"]})
        write(f"seeded-{slug}-control", {
            **common, "id": f"seeded-{slug}-control", "title": "Seeded-bug control: say NO FAULT",
            "why": f"Untouched {source}; the paired bug task changed line {n}. The answer is NO FAULT.",
            "prompt": prompt_for(args.hard, kinds, numbered(body)),
            "must_find": [{"any_of": ["no fault"]}],
            "must_not_claim": [{"regex": r"^\W*fault:\s*line", "label": "fault: line"}]})
        print(f"  {source}: line {n} {kind} {old.strip()!r} -> {change}  ->  seeded-{slug}-bug / -control")
    print("drafts written; review each bug task's `why`, then remove \"draft\": true")


if __name__ == "__main__":
    main()
