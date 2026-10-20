"""python suite/test_mine.py — mining a judged gateway call into draft tasks, on temp files."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mine  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    mine.CALLS = tmp / "calls.jsonl"
    mine.TASKS = tmp / "tasks"
    mine.offload.LOG = tmp / "offloads.jsonl"
    prompt = "Review this.\n\n===== pkg/a.py\nx = 1\n\n===== git diff b.py\n-y\n+z\n"
    mine.CALLS.write_text(json.dumps({"panel": "pX", "model": "bench/core", "prompt": prompt}) + "\n")
    mine.offload.add("m1", "t", None, "", "pX.member1")
    mine.offload.verdict("pX.member1", True, "", ["x is set to one"], ["x is never set"])

    assert mine.files_in(prompt) == {"pkg-a.py": "x = 1\n", "git-diff-b.py": "-y\n+z\n"}
    ids = mine.draft("pX")
    assert ids == ["mined-X-1", "mined-X-2"], ids
    true_task = json.loads((mine.TASKS / "mined-X-1" / "task.json").read_text())
    false_task = json.loads((mine.TASKS / "mined-X-2" / "task.json").read_text())
    assert true_task["draft"] and true_task["must_find"] == [{"any_of": ["verdict: true"]}]
    assert false_task["must_find"] == [{"any_of": ["verdict: false"]}]
    assert "x is never set" in false_task["prompt"]
    assert (mine.TASKS / "mined-X-1" / "fixtures" / "pkg-a.py").read_text() == "x = 1\n"
    # Mined once, it is not offered again.
    assert {c.rsplit(".", 1)[0] for c in mine.mined()} == {"pX"}
print("ok")
