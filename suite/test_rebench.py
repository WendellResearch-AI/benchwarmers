"""python suite/test_rebench.py — the diff filter, answer keys and window, no network."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import rebench  # noqa: E402

FILE = [f"line{i}" for i in range(1, 21)]
FILE[9] = "    if n > limit:"


def patch(path, hunk_lines, start=8):
    old = sum(1 for ln in hunk_lines if ln[:1] in (" ", "-"))
    new = sum(1 for ln in hunk_lines if ln[:1] in (" ", "+"))
    return (f"diff --git a/{path} b/{path}\nindex 1..2 100644\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -{start},{old} +{start},{new} @@ def f():\n" + "\n".join(hunk_lines) + "\n")


# A one-line fix: the key is the line it removed, and the after-file has the fix.
p = patch("pkg/mod.py", [" line8", " line9", "-    if n > limit:", "+    if n >= limit:", " line11"])
hunk, why = rebench.single_hunk(p)
assert hunk and hunk["path"] == "pkg/mod.py", why
after, keys = rebench.apply_fix(FILE, hunk)
assert keys == [10] and after[9] == "    if n >= limit:" and len(after) == len(FILE)

# A fix that only adds lines: either side of the gap.
add = patch("pkg/mod.py", [" line8", " line9", "+    check(n)", "     if n > limit:"])
after, keys = rebench.apply_fix(FILE, rebench.single_hunk(add)[0])
assert keys == [9, 10] and after[9] == "    check(n)" and len(after) == len(FILE) + 1

# Blank context lines, with or without their leading space, still match.
blank = list(FILE)
blank[8] = ""
for ctx in (" ", ""):
    p = patch("pkg/mod.py", [" line8", ctx, "-    if n > limit:", "+    if n >= limit:"])
    assert rebench.apply_fix(blank, rebench.single_hunk(p)[0])[1] == [10]

# Rejected: tests, non-Python, two files, two hunks, scattered changes, comment-only, too big.
ok = [" line8", "-    if n > limit:", "+    if n >= limit:"]
assert rebench.single_hunk(patch("tests/test_mod.py", ok))[0] is None
assert rebench.single_hunk(patch("README.md", ok))[0] is None
assert rebench.single_hunk(patch("a.py", ok) + patch("b.py", ok))[0] is None
two = patch("a.py", ok) + "@@ -15,1 +15,1 @@\n-line15\n+line15b\n"
assert rebench.single_hunk(two)[1] == "more than one hunk"
assert "contiguous" in rebench.single_hunk(patch("a.py", ["-line8", " line9", "+line9b"]))[1]
assert rebench.single_hunk(patch("a.py", ["-x = 1  # old", "+x = 1  # new"]))[1] == "only comments or whitespace"
assert "too large" in rebench.single_hunk(patch("a.py", [f"-l{i}" for i in range(6)] + ["+x"]))[1]
# Moving a line out of a block is a code change in Python, not whitespace.
assert rebench.single_hunk(patch("a.py", [" for x in xs:", "-        done()", "+    done()"]))[0] is not None
# Spacing inside a line is not.
assert rebench.single_hunk(patch("a.py", ["-x=1", "+x = 1"]))[1] == "only comments or whitespace"
# A blank context line that lost its trailing spaces still matches.
spaced = list(FILE)
spaced[8] = "    "
p = patch("pkg/mod.py", [" line8", " ", "-    if n > limit:", "+    if n >= limit:"])
assert rebench.apply_fix(spaced, rebench.single_hunk(p)[0])[1] == [10]

# A diff that does not match the file is refused, not applied.
assert rebench.apply_fix(["other"] * 20, rebench.single_hunk(patch("a.py", ok))[0]) is None

# Window: short files whole; long ones 400 lines around the change, numbered as in the file.
assert rebench.window(120, 50, 50) == (1, 120)
assert rebench.window(1000, 900, 901) == (601, 1000)
assert rebench.window(1000, 10, 10) == (1, 400)
assert rebench.window(1000, 500, 500) == (300, 699)
assert rebench.numbered(["a", "b", "c"], 2, 3) == "2| b\n3| c"
print("ok")
