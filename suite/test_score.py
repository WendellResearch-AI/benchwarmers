"""python suite/test_score.py — the scorer's negation guard and first-line scoring, no calls."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import run  # noqa: E402

trap = {"must_find": [{"any_of": ["NO DATABASE WRITES"]}], "must_not_claim": ["INSERT"],
        "min_answer_chars": 5}
# Ruling something out is not claiming it.
assert run.score(trap, "NO DATABASE WRITES: there are no INSERT or DELETE statements.")["invented"] == []
assert run.score(trap, "NO DATABASE WRITES. It does not INSERT anything.")["invented"] == []
# Asserting it still counts, including after an earlier negated sentence.
assert run.score(trap, "Line 12 runs an INSERT into settings.")["invented"] == ["INSERT"]
assert run.score(trap, "No reads here. Line 12 runs an INSERT.")["invented"] == ["INSERT"]

claim = {"must_find": [{"any_of": ["verdict: false"]}], "must_not_claim": ["verdict: true"],
         "score_in": "first_line", "min_answer_chars": 5}
assert run.score(claim, "VERDICT: FALSE\nquoted line ...")["found"] == 1
# Reasoning that mentions the other verdict later is not scored.
r = run.score(claim, "VERDICT: FALSE\nOne might think VERDICT: TRUE, but ...")
assert r["found"] == 1 and r["invented"] == []
# A verdict not on the first line does not count.
assert run.score(claim, "Let me think.\nVERDICT: FALSE")["found"] == 0
seeded = {"must_find": [{"regex": r"^fault:\s*line\s*1\b", "label": "fault: line 1"}],
          "must_not_claim": ["no fault"], "score_in": "first_line", "min_answer_chars": 5}
assert run.score(seeded, "FAULT: line 1\nthe comparison ...")["found"] == 1
assert run.score(seeded, "FAULT: line 12\n...")["found"] == 0      # not a prefix match
assert run.score(seeded, "NO FAULT")["invented"] == ["no fault"]
print("ok")
