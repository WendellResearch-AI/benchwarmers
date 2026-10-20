"""A scheduled suite run asks its model only what it has no answer for, six at most.

    python suite/test_pick.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run import PER_RUN, pick_tasks  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tasks = []
        for i in range(10):
            d = pathlib.Path(tmp) / f"t{i}"
            (d / "output").mkdir(parents=True)
            tasks.append({"id": f"t{i}", "dir": d})

        def answered(i, age):
            p = tasks[i]["dir"] / "output" / "m.md"
            p.write_text("answer", encoding="utf-8")
            os.utime(p, (time.time() - age, time.time() - age))

        for i in (0, 1, 2, 3):
            answered(i, 100 * i)
        picked = [t["id"] for t in pick_tasks(tasks, "m")]
        assert picked == ["t4", "t5", "t6", "t7", "t8", "t9"], picked       # gaps first, capped
        assert len(pick_tasks(tasks, "m")) == PER_RUN == 6
        for i in range(4, 10):
            answered(i, 1000 + i)                                            # all answered now
        picked = [t["id"] for t in pick_tasks(tasks, "m", n=3)]
        assert picked == ["t9", "t8", "t7"], picked                          # then the oldest
        # A saved rate limit is a gap again; a timeout (the model's own) is not.
        (tasks[2]["dir"] / "output" / "m.md").write_text(
            "Error: Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day'}}",
            encoding="utf-8")
        (tasks[3]["dir"] / "output" / "m.md").write_text("<<TIMED OUT after 300s>>", encoding="utf-8")
        assert [t["id"] for t in pick_tasks(tasks, "m")] == ["t2"], pick_tasks(tasks, "m")
        assert [t["id"] for t in pick_tasks(tasks, "other", n=2)] == ["t0", "t1"]   # per model
    print("ok")


if __name__ == "__main__":
    main()
