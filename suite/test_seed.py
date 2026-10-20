"""python suite/test_seed.py — each kind of seeded change on a tiny source, no calls."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import seed  # noqa: E402

SRC = '''def total(items, limit):
    n = 0
    for i in range(len(items)):
        if items[i] is None:
            continue
        n += items[i]
        if n > limit:
            break
    rest = items[1:]
    head = items[:2]
    record(n, limit)
    print(n)
    return max(n, 0)


if __name__ == "__main__":
    total([1], 2)
'''
BODY = SRC.splitlines()


def changes(kind):
    """(line number, changed line or None if deleted) for every candidate of a kind."""
    out = []
    for c in seed.candidates(BODY, (kind,)):
        after = seed.apply(BODY, c)
        assert after is not None and after != BODY and seed.parses(after), (kind, c)
        out.append((c[1] + 1, None if c[4] is None else after[c[1]].strip()))
    return out


# The old token kinds still work, and the __main__ block is never touched.
assert (7, "if n >= limit:") in changes("compare")
assert all(line < 16 for line, _ in changes("compare") + changes("number"))
# number: small ints shift by one or go to 0; none of the __main__ block's.
assert (2, "n = 1") in changes("number") and (9, "rest = items[0:]") in changes("number")
# args: adjacent positional arguments swap; max() is symmetric, so never.
assert changes("args") == [(11, "record(limit, n)")], changes("args")
# off-by-one: range() and one-sided slices.
assert {(3, "for i in range(len(items) - 1):"), (3, "for i in range(1, len(items)):"),
        (9, "rest = items[2:]"), (10, "head = items[:1]")} <= set(changes("off-by-one"))
assert not any("1 + 1" in after or "2 - 1" in after for _, after in changes("off-by-one"))
signed = ["def f(s):", "    return s[:+1] + s[-2:]"]
assert {seed.apply(signed, c)[1].strip() for c in seed.candidates(signed, ("off-by-one",))} == \
    {"return s[:0] + s[-2:]", "return s[:+1] + s[-1:]"}
# round()'s digits only change how a number prints: not offered.
assert seed.candidates(["x = round(y, 2)"], ("number",)) == []
# break-continue both ways.
assert set(changes("break-continue")) == {(5, "break"), (8, "continue")}
# remove-call deletes record(...); print is too quiet to count.
assert changes("remove-call") == [(11, None)], changes("remove-call")
cand = seed.candidates(BODY, ("remove-call",))[0]
assert seed.answer_lines(cand, len(BODY) - 1) == [10, 11]
# augassign.
assert changes("augassign") == [(6, "n -= items[i]")]
# A call that is the only statement of its block becomes `pass`, so the block stays valid.
only = ["def f(x):", "    if x:", "        g(x)", "    return x"]
c = seed.candidates(only, ("remove-call",))
assert [seed.apply(only, x)[2].strip() for x in c] == ["pass"], c
# Code after a return is dead: nothing there is offered.
dead = ["def f(x):", "    return x", "    x += 1"]
assert seed.candidates(dead, ("augassign",)) == []
# Indented functions (methods) keep their columns.
method = ["    def m(self, a, b):", "        self.go(a, b)", "        return a < b"]
got = {seed.apply(method, x)[1] for x in seed.candidates(method, ("args",))}
assert got == {"        self.go(b, a)"}, got
# The prompt names the kinds in function mode, and keeps the first batches' wording for token kinds.
p = seed.prompt_for(False, list(seed.TOKEN_KINDS), "CODE")
assert "ONE token" in p and "(a comparison operator, `and`/`or`, a returned True/False, or a removed `not`)" in p
assert "line just before or after the gap" in seed.prompt_for(True, list(seed.KINDS), "CODE")
print("ok")
