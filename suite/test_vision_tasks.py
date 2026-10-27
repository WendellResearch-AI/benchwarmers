"""The vision tasks: each answer key takes a right answer whole and catches a
guess on its trap; a vision task goes only to a model that takes images, a
text task only to one that is not images only; the shipped tasks reach the
data folder without touching its saved answers.

    python suite/test_vision_tasks.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run  # noqa: E402

RIGHT = {
    "vision-receipt": "store: Harbor Lane Grocery\ndate: 2026-03-14\nitems: 4\npriciest: Cheddar 200g\n"
                      "total: $22.17\ntip: not shown",
    "vision-label": "**grader:** Northline Grading\ngrade: MINT 9\nyear: 2019\nnumber: #148\nplayer: Maren Okafor\n"
                    "cert: 40731958\nsurface: 8.5\nautograph: no",
    "vision-bars": "tallest: Billing\nshortest: Data\nweb: 35\nover_30: 2\ntarget: not shown",
    "vision-table": "south_q3: 1,120\nwest_q1: 870\ncentral_q3: 1098\nq4_highest: East\nnortheast_q2: not shown",
    "vision-screen": "toggles_on: Back up photos, Notify on conflicts\nconflict: Keep both\ndisabled: Sync now\n"
                     "error_code: E-213\naccount: not shown",
    "vision-shapes": "- blue_circles: 6\n- red_squares: 3\n- green_triangles: 2\n- top_right: yellow star\n"
                     "- purple_shapes: 0",
    "vision-invoice": "invoice: INV-2026-0417\ndue: 2026-05-02\namount_due: EUR 1,284.50\nbill_to: Kestrel Labs BV\n"
                      "po_number: not shown",
}
# Each one guesses the trap, and gets one field wrong besides.
GUESS = {
    "vision-receipt": RIGHT["vision-receipt"].replace("tip: not shown", "tip: $3.00").replace("items: 4", "items: 5"),
    "vision-label": RIGHT["vision-label"].replace("autograph: no", "autograph: yes").replace("40731958", "40781958"),
    "vision-bars": RIGHT["vision-bars"].replace("target: not shown", "target: 40").replace("over_30: 2", "over_30: 3"),
    "vision-table": RIGHT["vision-table"].replace("northeast_q2: not shown", "northeast_q2: 1,210")
                                         .replace("central_q3: 1098", "central_q3: 1160"),
    "vision-screen": RIGHT["vision-screen"].replace("account: not shown", "account: sam@example.com")
                                           .replace("Notify on conflicts", "Notify on conflicts, Sync on mobile data"),
    "vision-shapes": RIGHT["vision-shapes"].replace("purple_shapes: 0", "purple_shapes: 1")
                                           .replace("blue_circles: 6", "blue_circles: 5"),
    "vision-invoice": RIGHT["vision-invoice"].replace("po_number: not shown", "po_number: 7781")
                                             .replace("due: 2026-05-02", "due: 2026-04-02"),
}


def main() -> None:
    specs = {}
    for path in sorted((HERE / "tasks").glob("vision-*/task.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["dir"] = path.parent
        specs[spec["id"]] = spec
    assert set(specs) == set(RIGHT), sorted(specs)
    for tid, spec in specs.items():
        assert spec["shape"] == "vision" and spec["images"], tid
        assert all((spec["dir"] / "fixtures" / n).exists() for n in spec["images"]), tid
        good = run.score(spec, RIGHT[tid])
        assert good["kind"] == "answered" and good["found"] == good["of"] and not good["invented"], (tid, good)
        bad = run.score(spec, GUESS[tid])
        assert bad["found"] < bad["of"] and bad["invented"], (tid, bad)
        assert run.image_urls(spec)[0].startswith("data:image/png;base64,"), tid

    # Who each task is for: pictures to a model that takes them, text to one that is not images only.
    text, pic = {"id": "t"}, {"id": "v", "images": ["x.png"]}
    skip, seeing = {"eyes-only"}, {"eyes-only": True, "general-sees": True, "general-blind": False}
    assert run.applies(text, "general-sees", skip, seeing) and run.applies(pic, "general-sees", skip, seeing)
    assert run.applies(text, "general-blind", skip, seeing) and not run.applies(pic, "general-blind", skip, seeing)
    assert not run.applies(text, "eyes-only", skip, seeing) and run.applies(pic, "eyes-only", skip, seeing)

    # The shipped tasks reach the data folder; its saved answers stay.
    kept = run.TASKS
    with tempfile.TemporaryDirectory() as tmp:
        run.TASKS = pathlib.Path(tmp)
        try:
            saved = run.TASKS / "vision-bars" / "output" / "m.md"
            saved.parent.mkdir(parents=True)
            saved.write_text("an answer", encoding="utf-8")
            (run.TASKS / "vision-bars" / "task.json").write_text("{}", encoding="utf-8")   # an old key
            run.seed_shipped()
            for tid in RIGHT:
                assert (run.TASKS / tid / "task.json").read_bytes() == (HERE / "tasks" / tid / "task.json").read_bytes()
            assert saved.read_text(encoding="utf-8") == "an answer"
        finally:
            run.TASKS = kept
    panel()
    print("ok")


def panel() -> None:
    """serve.vision_rows: the tasks field by field, and real work by how it was judged."""
    sys.path.insert(0, str(HERE.parent))
    import offload
    import serve
    kept = serve.DATA, serve.on_bench, offload.LOG, serve._vision_work
    with tempfile.TemporaryDirectory() as tmp:
        data = pathlib.Path(tmp)
        for tid in ("vision-bars", "vision-shapes"):
            out = data / "suite" / "tasks" / tid / "output"
            out.mkdir(parents=True)
            (out.parent / "task.json").write_bytes((HERE / "tasks" / tid / "task.json").read_bytes())
            (out / "sharp.md").write_text(RIGHT[tid], encoding="utf-8")
            (out / "guesser.md").write_text(GUESS[tid], encoding="utf-8")
            (out / "gone.md").write_text(RIGHT[tid], encoding="utf-8")            # not on the bench now
        (data / "suite" / "tasks" / "vision-bars" / "output" / "down.md").write_text(
            "Error: Error code: 503 - service unavailable", encoding="utf-8")
        seats = [{"call": "p1.m1", "alias": "sharp", "status": "ok", "answer": "x", "seconds": 4},
                 {"call": "p1.m2", "alias": "guesser", "status": "ok", "answer": "x", "seconds": 9},
                 {"call": "p2.m1", "alias": "sharp", "status": "ok", "answer": "x", "seconds": 6},
                 {"call": "p2.m2", "alias": "guesser", "status": "error", "answer": "", "seconds": 1},
                 {"call": "p3.m1", "alias": "sharp", "status": "ok", "answer": "x", "seconds": 5}]
        (data / "runs" / "gateway").mkdir(parents=True)
        (data / "runs" / "gateway" / "calls.jsonl").write_text(
            json.dumps({"shape": "vision", "seats": seats[:2]}) + "\n" + json.dumps({"shape": "vision", "seats": seats[2:4]})
            + "\n" + json.dumps({"shape": "general", "seats": [dict(seats[0], call="p9.m1")]})
            + "\n" + json.dumps({"shape": "vision", "seats": seats[4:]}) + "\n", encoding="utf-8")
        (data / "metrics").mkdir()
        log = data / "metrics" / "offloads.jsonl"
        log.write_text("\n".join(json.dumps(e) for e in (
            {"date": "2026-10-27", "model": "sharp", "task": "t", "call": "p1.m1", "useful": True,
             "real": ["grade=9", "cert=1"], "false": []},
            {"date": "2026-10-27", "model": "guesser", "task": "t", "call": "p1.m2", "useful": False,
             "real": ["grade=9"], "false": ["cert=2"]},
            {"date": "2026-10-27", "model": "sharp", "task": "t", "call": "p2.m1", "useful": True,
             "note": "agreed with another seat"})) + "\n", encoding="utf-8")
        serve.DATA, offload.LOG, serve._vision_work = data, log, (None, {})
        serve.on_bench = lambda: {"sharp", "guesser", "down"}
        try:
            v = serve.vision_rows()
        finally:
            serve.DATA, serve.on_bench, offload.LOG, serve._vision_work = kept
    s = v["summary"]
    assert set(s) == {"sharp", "guesser", "down"}, s                                  # "gone" is not on the bench
    assert s["sharp"] == {"asked": 2, "answered": 2, "fields": 10, "right": 10, "traps": 2, "taken": 0}, s["sharp"]
    assert s["guesser"]["right"] == 6 and s["guesser"]["taken"] == 2, s["guesser"]
    assert s["down"] == {"asked": 1, "answered": 0, "fields": 0, "right": 0, "traps": 0, "taken": 0}, s["down"]
    states = {(h["model"], h["task"], h["field"]): h["state"] for h in v["heat"]}
    assert states[("guesser", "vision-bars", "over_30")] == "misread"
    assert states[("guesser", "vision-bars", "target")] == "trap taken"
    assert states[("down", "vision-bars", "web")] == "no answer"
    w = v["work"]
    assert w["sharp"]["checked, helped"] == 1 and w["sharp"]["useful, no claims"] == 1 and w["sharp"]["not judged"] == 1, w
    assert (w["sharp"]["real"], w["sharp"]["false"], w["sharp"]["seconds"]) == (2, 0, 5.0), w["sharp"]
    assert w["guesser"]["checked, did not help"] == 1 and w["guesser"]["no answer"] == 1, w["guesser"]
    assert (w["guesser"]["real"], w["guesser"]["false"]) == (1, 1), w["guesser"]


if __name__ == "__main__":
    main()
