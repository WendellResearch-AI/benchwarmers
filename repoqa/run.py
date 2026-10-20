"""The LONG-CONTEXT tier: RepoQA, ON DEMAND.

    python repoqa/run.py --model kimi-k3               # 20 calls, python
    python repoqa/run.py --model kimi-k3 --repos 10    # 100, a full language
    python repoqa/run.py --model kimi-k3 --languages python,rust

WHAT IT MEASURES THAT NOTHING ELSE HERE DOES
--------------------------------------------
Whether a model actually READS a long document, or loses the middle of it.

Every task is Searching Needle Function: ~16k tokens of real source, ordered
by import dependency, plus a precise natural-language description of ONE
function inside it. Name that function. That is the shape of the work these
models are actually given -- "list every place a user can edit a value in
these three files" -- and no other tier here measures it.

`perf/` sends a large prompt but only TIMES it; it never checks whether the
answer is right. `suite/` checks correctness on our own repos but is six
tasks. So nothing here could tell a model that reads 40KB from one that skims
it, while the real offloads run 7.9k-60k characters.

ON DEMAND, NOT ON A TIMER. `suite/` holds the scheduled slot because it is
uncontaminated and it is our own work. Schedule this later if the internal
tests turn out to need a companion.

HOW TO READ IT -- DOWNWARD, LIKE FLOOR
--------------------------------------
RepoQA is 2024 and built from public repositories, so it fails the date check
in BENCHMARKS.md. A LOW score is real evidence: the model could not find a
function it was handed and described. A HIGH score proves less.

It is still a better floor than `floor/` was, for one reason: HumanEval asks
for a small isolated function and memorisation answers it outright, whereas
here THE ANSWER IS IN THE PROMPT and the question is whether the model can
find it. Contamination helps less with retrieval than with recall.

Scoring is objective and needs no judge -- an answer counts when it best
matches the ground-truth function and clears a similarity threshold.

WHY GENERATION DOES NOT USE `repoqa.search_needle_function`
-----------------------------------------------------------
`repoqa/provider/request/openai.py` calls `signal.alarm`, which does not exist
on Windows -- the SAME wall `evalplus.codegen` presented, from the same
authors, and the same answer applies: generation runs through the `llm` CLI
path that `suite/` and `floor/` already use, and RepoQA is used for the part
carrying the value, which is building the contexts and scoring the answers.

That is better than patching theirs, for the reason floor/ gives: our path
tells a refusal apart from an upstream error apart from a timeout, so a model
that was rate-limited is not recorded as one that could not find the function.

WHY ONLY REPOSITORIES ARE TRIMMED, NEVER NEEDLES
------------------------------------------------
A full sweep is 500 tasks -- 5 languages x 10 repos x 10 needles -- at 16k
tokens each, which is three floor runs. `--repos` takes a slice.

Needles are NOT trimmable on purpose. RepoQA plants the i-th needle at
position `(i + 0.5) / len(needles)` through the context, so the ten needles of
a repository sweep the document from front to back. Keeping fewer would only
sample the front, and WHERE the needle sits is precisely what this tier exists
to measure. Ten needles is one repository is one full position sweep.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = ROOT / "runs"
OUT = HERE / "results"

sys.path.insert(0, str(ROOT / "suite"))
from run import ask, llm_binary, outcome  # noqa: E402
from run import available_models  # noqa: E402


def build_tasks(languages: list[str], repos: int,
                context_size: int) -> list[dict]:
    """RepoQA's own context construction, with our own slice of the dataset.

    Mirrors `search_needle_function.evaluate_model`'s task loop. Their
    `make_code_context` does the work that matters -- ordering files by
    dependency and placing the needle at the requested depth -- and is
    reachable without importing the provider that cannot run here.
    """
    from repoqa.data import get_repoqa_data
    from repoqa.search_needle_function import (INSTRUCTION, TEMPLATE,
                                               make_code_context)
    from repoqa.utility import topological_sort

    data = get_repoqa_data()
    tasks: list[dict] = []
    for lang in languages:
        if lang not in data:
            sys.exit(f"unknown language {lang!r}; have {sorted(data)}")
        for repo in data[lang][:repos]:
            if "needles" not in repo:
                continue
            ordered = topological_sort(repo["dependency"])
            files = [(p, repo["content"][p]) for p in ordered]
            for i, needle in enumerate(repo["needles"]):
                # The needle's depth through the document. Computed from the
                # FULL needle list, never a trimmed one -- see the module note.
                position_ratio = (i + 0.5) / len(repo["needles"])
                task = {
                    "repo": repo["repo"],
                    "name": needle["name"],
                    "language": lang,
                    "path": needle["path"],
                    "position_ratio": position_ratio,
                    "description":
                        f"\nFunction Description:{needle['description']}\n",
                    "instruction": INSTRUCTION,
                    "template": TEMPLATE,
                }
                task.update(make_code_context(
                    needle, files, position_ratio=position_ratio,
                    code_context_size=context_size, language=lang))
                tasks.append(task)
    return tasks


def prompt_for(task: dict) -> str:
    """RepoQA's own template: instruction, context, description, instruction."""
    return "".join(task[key] for key in task["template"].split("\n"))


def scorer_python() -> str:
    """The interpreter that can run `repoqa.compute_score`.

    A SECOND VENV, for the reason `funcbench/tool/` has one: a dependency
    conflict that cannot be resolved in the main environment. RepoQA scores
    through `tree_sitter_languages`, which was built against tree-sitter
    < 0.22 and raises `TypeError: __init__() takes exactly 1 argument` on
    anything newer. The main venv has tree-sitter 0.26 because EVALPLUS
    requires it, so downgrading to satisfy RepoQA would break `floor/`.

    Only scoring is isolated. Context building imports the same package
    happily -- it never reaches a parser -- and generation goes through the
    `llm` CLI, so the split costs nothing and keeps both tiers working.
    """
    local = HERE / ".venv" / "Scripts" / "python.exe"
    return str(local) if local.exists() else sys.executable


def score(path: pathlib.Path) -> str:
    """RepoQA's scorer — the half of it that carries the value."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # RICH TRUNCATES TO THE TERMINAL WIDTH, and the score table has twelve
    # columns. Without this the run note records `10…` where every number
    # should be -- the same way floor/ once recorded a row of hashes.
    env["COLUMNS"] = "200"
    # `compute_score` asks "Press [Y/N] to overwrite" when its SCORES file
    # already exists, and there is no stdin here, so it printed the table and
    # then died on EOFError -- putting a traceback in the run note under a
    # perfectly good score. Clearing it first means it never asks.
    stale = path.with_name(path.stem + "-SCORES.json")
    stale.unlink(missing_ok=True)
    proc = subprocess.run(
        [scorer_python(), "-m", "repoqa.compute_score", str(path)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env)
    noise = ("it/s", "%|", "PyTorch was not found", "Downloading",
             "FutureWarning", "warn(", "Scoring ")
    keep = [ln for ln in (proc.stdout + proc.stderr).splitlines()
            if ln.strip() and not any(n in ln for n in noise)]
    if proc.returncode != 0 and not keep:
        return f"scoring FAILED (exit {proc.returncode})"
    return "\n".join(keep)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True,
                    help="llm CLI alias (not the served name)")
    ap.add_argument("--languages", default="python",
                    help="comma-separated: python,cpp,java,typescript,rust")
    ap.add_argument("--repos", type=int, default=2,
                    help="repositories per language, max 10. Each is 10 "
                         "needles sweeping the document front to back, so "
                         "the default is 20 calls")
    ap.add_argument("--context", type=int, default=16384,
                    help="code context size in tokens")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--score-only", action="store_true",
                    help="re-score answers already generated. Generation is "
                         "the expensive half and should never be repeated "
                         "for a scoring bug — the lesson floor/ records")
    args = ap.parse_args()

    # Named explicitly, so an ad hoc model is allowed here: this is the ask.
    if args.model not in available_models(include_ad_hoc=True):
        sys.exit(f"{args.model} is not available. See scripts/list_served.py")
    languages = [s.strip() for s in args.languages.split(",") if s.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / f"{args.model}.jsonl"

    non_answers: list[tuple[str, str, str]] = []
    if args.score_only:
        if not out_path.exists():
            sys.exit(f"no answers to score at {out_path}")
        tasks = [json.loads(ln) for ln
                 in out_path.read_text(encoding="utf-8").splitlines() if ln]
        for t in tasks:
            res = outcome((t.get("output") or [""])[0])
            if res:
                non_answers.append((t["name"], res[0], res[1]))
        print(f"scoring {len(tasks)} existing answers from {out_path}\n")
        return finish(args, languages, tasks, out_path, non_answers,
                      counted=False)

    print(f"building contexts for {', '.join(languages)} "
          f"x {args.repos} repos, {args.context} tokens each...")
    tasks = build_tasks(languages, args.repos, args.context)
    print(f"{len(tasks)} tasks for {args.model}\n")

    binary = llm_binary()
    # Written as each answer arrives, for the reason floor/ learned the hard
    # way: a long run that is interrupted should keep what it already paid for.
    non_answers = []
    with out_path.open("w", encoding="utf-8") as fh:
        for i, task in enumerate(tasks, 1):
            body, seconds, attempts = ask(binary, args.model,
                                          prompt_for(task), args.timeout)
            res = outcome(body)
            kind, detail = res if res else ("answered", "")
            if kind != "answered":
                # NOT a wrong answer. A model that was throttled or timed out
                # did not fail to find the function, and scoring it as though
                # it did is the confusion this repo exists to avoid. It still
                # needs a row, and the count is reported separately.
                non_answers.append((task["name"], kind, detail))
            fh.write(json.dumps({**task, "output": [body]}) + "\n")
            fh.flush()
            print(f"  [{i}/{len(tasks)}] {task['repo']}:{task['name']} "
                  f"pos={task['position_ratio']:.2f} {kind} "
                  f"({seconds:.0f}s, attempt {attempts})", flush=True)

    return finish(args, languages, tasks, out_path, non_answers, counted=True)


def finish(args, languages: list[str], tasks: list[dict],
           out_path: pathlib.Path,
           non_answers: list[tuple[str, str, str]], counted: bool) -> None:
    """Score, record the calls, and write the run note.

    Shared with `--score-only`, which must produce the same note: a re-scored
    run is still a result, and a score that only ever reached a terminal is
    not written down anywhere.

    `counted` is False when re-scoring, because those calls were already
    billed to the ledger the first time and counting them twice would report
    a benchmark sweep that never happened.
    """
    report = score(out_path)
    print("\n" + report)

    # MEASUREMENT, not work, so the usage counter does not read a benchmark
    # sweep as reliance on a model.
    if counted:
        try:
            sys.path.insert(0, str(ROOT / "metrics"))
            from cost import note_benchmark_calls
            note_benchmark_calls(args.model, len(tasks))
        except Exception:                          # noqa: BLE001
            pass                                   # a counter is not the job

    stamp = dt.date.today().isoformat()
    note = RUNS / f"repoqa-{stamp}.md"
    with note.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## RepoQA {stamp} — `{args.model}`\n\n")
        # COUNTED FROM THE TASKS, not from the flags. `--score-only` does not
        # know what the original run was given, and taking `--repos` from its
        # default wrote "2 repos" onto a run that used one.
        langs = sorted({t["language"] for t in tasks})
        repos = {(t["language"], t["repo"]) for t in tasks}
        ntokens = max((t.get("code_context_ntokens") or 0) for t in tasks)
        fh.write(f"{len(tasks)} tasks: {', '.join(langs)} × "
                 f"{len(repos)} repos, up to {ntokens:,} tokens of context.\n\n")
        fh.write("```\n" + report.strip() + "\n```\n\n")
        if non_answers:
            fh.write(f"**{len(non_answers)} did not answer** — not scored as "
                     "wrong, because a throttled or timed-out call is not a "
                     "model that failed to find the function:\n")
            for name, kind, detail in non_answers[:10]:
                fh.write(f"- `{name}`: {kind} — {detail}\n")
            fh.write("\n")
        fh.write("**Read this DOWNWARD.** RepoQA is 2024 and built from "
                 "public repositories, so a low score is real evidence and a "
                 "high one proves less. It is a better floor than HumanEval "
                 "was because the answer is in the prompt: the question is "
                 "whether the model can find it, and contamination helps less "
                 "with retrieval than with recall.\n")
    print(f"\nappended {note}")


if __name__ == "__main__":
    main()
