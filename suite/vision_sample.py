"""Vision tasks sampled from public datasets, each item's answer verified by people.

    python suite/vision_sample.py --count 5                  # five of each source
    python suite/vision_sample.py --source cord --count 3    # one source
    python suite/vision_sample.py --dry-run                  # what would be written

The made pictures (suite/vision_tasks.py) are clean renders: exact keys, never
seen in training, but no blur, glare or crumpled paper. These are the real
thing, read through Hugging Face's datasets-server like suite/rebench.py, no
`datasets` package:

  cord      naver-clova-ix/cord-v2 (CC BY 4.0): photographed receipts with
            their totals, cash and change as annotated. Cash receipts only, so
            a card number is a field the picture does not show.
  screenqa  bevaya/RICO-ScreenQA (CC BY 4.0): app screenshots, two questions
            people answered from the screen and one they marked `<no answer>`.
  tablevqa  terryoo/TableVQA-Bench (CC BY 4.0): tables from financial reports
            and Wikipedia as images, a short answer each.
  pope      lmms-lab-encoder/POPE (no licence on its card; COCO photos): "is
            there a <thing> in the image?", two present and two absent, the
            absent ones chosen to be likely (its adversarial split). A yes to
            an absent thing is the invention.

Written to the data folder, never the repository: each install samples its
own, so nothing of theirs is redistributed. Written ready to run, not as
drafts: the answers are the datasets' own, checked by people. Look at a few
anyway (`--dry-run` prints each key); a source's quirks show in its first items.

POPE's photos are often in training sets (COCO is everywhere); the receipts,
screens and tables less so, and the made pictures and your own photos not
at all. The Vision panel shows each task, so a source that is too easy shows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rebench import api  # noqa: E402  -- datasets-server JSON, retrying its busy 500s
from vision_keys import NOT_SHOWN, PROMPT, clause, key, not_shown, number, phrase  # noqa: E402

sys.path.insert(0, str(HERE.parent / "metrics"))
from paths import DATA  # noqa: E402
TASKS = DATA / "suite" / "tasks"
PAGE = 100                                           # datasets-server's largest page


def rows(dataset: str, config: str, split: str, offset: int, length: int) -> list[dict]:
    out = []
    while len(out) < length:
        page = api("rows", dataset=dataset, config=config, split=split, offset=offset + len(out),
                   length=min(PAGE, length - len(out)))
        got = [r["row"] for r in page.get("rows", [])]
        out += got
        if len(got) < PAGE:
            break
    return out


def image(row: dict, field: str = "image") -> tuple[bytes, str]:
    """The row's picture, fetched now: datasets-server's links expire."""
    src = row[field]["src"]
    req = urllib.request.Request(src, headers={"User-Agent": "benchwarmers"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    ext = pathlib.PurePosixPath(urllib.parse.urlparse(src).path).suffix.lower() or ".jpg"
    return data, ext


def alternatives(name: str, answers: list[str], label: str) -> dict:
    """Any of several accepted answers, anywhere on the field's line."""
    alts = "|".join(phrase(a)[2:] for a in answers)
    return clause(name, rf".*(?:{alts})", label)


def spec(task_id: str, title: str, source: str, dataset: str, row_ref: str, licence: str,
         lines: list[str], find: list[dict], not_claim: list[dict], why: str) -> dict:
    return {"id": task_id, "title": title, "shape": "vision", "prompt": PROMPT.format(lines="\n".join(lines)),
            "min_answer_chars": 8, "must_find": find, "must_not_claim": not_claim, "why": why,
            "source": f"{dataset} {row_ref}", "source_url": f"https://huggingface.co/datasets/{dataset}",
            "license": licence, "made_by": f"suite/vision_sample.py --source {source}"}


# ---- the sources: each yields (spec without images, picture bytes, extension) ----

def cord(offset: int, scan: int):
    """Cash receipts: total, cash given, change, how many menu lines."""
    ds = "naver-clova-ix/cord-v2"
    for i, r in enumerate(rows(ds, "default", "test", offset, scan)):
        gt = json.loads(r["ground_truth"]).get("gt_parse", {})
        menu, total = gt.get("menu"), gt.get("total") or {}
        menu = [menu] if isinstance(menu, dict) else menu or []
        paid = {k: total.get(k) for k in ("total_price", "cashprice", "changeprice")}
        if (not all(isinstance(v, str) and re.search(r"\d", v) for v in paid.values())
                or any(k in total for k in ("creditcardprice", "emoneyprice"))
                or not 1 <= len(menu) <= 8 or any("sub" in m for m in menu)):
            continue
        find = [clause("total", number(paid["total_price"]), f"total: {paid['total_price']}"),
                clause("cash", number(paid["cashprice"]), f"cash: {paid['cashprice']}"),
                clause("change", number(paid["changeprice"]), f"change: {paid['changeprice']}"),
                clause("items", rf"{len(menu)}\b", f"items: {len(menu)}")]
        trap_find, trap_claim = not_shown("card")
        yield (spec(f"vision-cord-{offset + i}", "Read a photographed receipt", "cord", ds, f"test row {offset + i}",
                    "CC BY 4.0",
                    ["total: <the total to pay>", "cash: <cash given>", "change: <change given back>",
                     "items: <how many different items are listed>", "card: <the last four digits of the card used>"],
                    find + [trap_find], [trap_claim],
                    "A receipt as photographed, from CORD v2 (Park et al., 2019). Paid in cash, so no card is"
                    " shown. Items: " + "; ".join(f"{m.get('nm', '?')} x{m.get('cnt', '?')}" for m in menu)),
               *image(r))


def screenqa(offset: int, scan: int):
    """App screens: two questions answered from the screen, one it cannot answer."""
    ds = "bevaya/RICO-ScreenQA"
    screens: dict[str, list[dict]] = {}
    for r in rows(ds, "default", "test", offset, scan):
        screens.setdefault(r["screen_id"], []).append(r)
    for sid, qs in screens.items():
        def short(q):
            return sorted({g["ui_elements"][0]["text"].strip() for g in q["ground_truth"] if g["ui_elements"]})
        answered = [q for q in qs if 1 <= len(short(q)) <= 2 and all(0 < len(a) <= 40 for a in short(q))]
        blank = [q for q in qs if all(g["full_answer"] == "<no answer>" for g in q["ground_truth"])]
        if len(answered) < 2 or not blank:
            continue
        picked = answered[:2]
        at = int(hashlib.sha1(sid.encode()).hexdigest(), 16) % 3      # where the unanswerable one sits
        picked.insert(at, blank[0])
        lines, find, claim = [], [], []
        for n, q in enumerate(picked, 1):
            name = f"q{n}"
            lines.append(f"{name}: <answer to: {q['question']}>")
            if q is blank[0]:
                f, c = not_shown(name)
                find.append(f)
                claim.append(c)
            else:
                find.append(alternatives(name, short(q), f"{name}: {' or '.join(short(q))}"))
        yield (spec(f"vision-screenqa-{sid}", "Answer questions about an app screen", "screenqa", ds,
                    f"screen {sid}", "CC BY 4.0", lines, find, claim,
                    "A RICO app screenshot with questions from ScreenQA (Hsiao et al., 2022); people marked one"
                    " as having no answer on this screen."),
               *image(picked[0]))


def tablevqa(offset: int, scan: int):
    """Tables as images: one question, one short answer."""
    ds = "terryoo/TableVQA-Bench"
    splits = ("fintabnetqa", "vwtq")
    pages = [list(enumerate(rows(ds, "default", s, offset, scan // 2))) for s in splits]
    # The two kinds in turn: a report's table, then a Wikipedia one.
    for (i, r), split in ((item, s) for pair in zip(*pages) for item, s in zip(pair, splits)):
        gt = (r.get("gt") or "").strip().rstrip(".")
        if not gt or len(gt) > 24 or "," in gt and not re.fullmatch(r"[\d,.$%()-]+", gt):
            continue
        value = number(gt) if re.fullmatch(r"[\d,.$%()\s-]+", gt) and re.search(r"\d", gt) else phrase(gt)[2:]
        yield (spec(f"vision-tablevqa-{split}-{offset + i}", "Read a table", "tablevqa", ds,
                    f"{split} row {offset + i}", "CC BY 4.0", [f"answer: <answer to: {r['question']}>"],
                    # Anywhere on the line, but whole: 3.14 is not the end of 13.14.
                    [clause("answer", rf".*(?<![\w.,]){value}", f"answer: {gt}")], [],
                    f"A table from {'a financial report (FinTabNet)' if split == 'fintabnetqa' else 'Wikipedia (WTQ)'},"
                    " via TableVQA-Bench (Kim et al., 2024)."),
               *image(r))


def pope(offset: int, scan: int):
    """Photos: is there a <thing>? Two present, two absent but likely."""
    ds = "lmms-lab-encoder/POPE"
    photos: dict[str, list[dict]] = {}
    for r in rows(ds, "Full", "adversarial", offset, scan):
        photos.setdefault(r["image_source"], []).append(r)
    for src, qs in photos.items():
        yes = [q for q in qs if q["answer"].strip().lower() == "yes"][:2]
        no = [q for q in qs if q["answer"].strip().lower() == "no"][:2]
        if len(yes) < 2 or len(no) < 2:
            continue
        order = [yes[0], no[0], no[1], yes[1]] if int(hashlib.sha1(src.encode()).hexdigest(), 16) % 2 else \
            [no[0], yes[0], yes[1], no[1]]
        lines, find, claim = [], [], []
        for n, q in enumerate(order, 1):
            name, thing = f"q{n}", re.sub(r"^is there an? |\s*in the image\?$", "", q["question"].lower())
            lines.append(f"{name}: <yes or no: {q['question']}>")
            find.append(clause(name, rf"{q['answer'].lower()}\b", f"{name}: {q['answer'].lower()} ({thing})"))
            if q["answer"].lower() == "no":
                claim.append(clause(name, r"yes\b", f"{name}: yes, a {thing} (there is none)"))
        yield (spec(f"vision-pope-{src.rsplit('_', 1)[-1].lstrip('0')}", "Say which things a photo shows", "pope", ds,
                    src, "no licence on the dataset card; COCO photos", lines, find, claim,
                    f"COCO photo {src}, questions from POPE's adversarial split (Li et al., 2023): the absent"
                    " things are ones that often appear with what is there."),
               *image(qs[0]))


SOURCES = {"cord": cord, "screenqa": screenqa, "tablevqa": tablevqa, "pope": pope}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ap.add_argument("--count", type=int, default=5, help="tasks to write per source")
    ap.add_argument("--offset", type=int, default=0, help="start this far into each source's rows")
    ap.add_argument("--scan", type=int, default=300, help="rows to read per source, at most")
    ap.add_argument("--dry-run", action="store_true", help="fetch and print each key, write nothing")
    args = ap.parse_args()
    for name in SOURCES if args.source == "all" else [args.source]:
        made = 0
        for s, picture, ext in SOURCES[name](args.offset, args.scan):
            folder = TASKS / s["id"]
            if folder.exists():
                continue                                  # sampled before: kept, answers and all
            made += 1
            print(f"{s['id']}: " + "; ".join(c["label"] for c in s["must_find"]))
            if not args.dry_run:
                (folder / "fixtures").mkdir(parents=True)
                s["images"] = [f"{s['id'].removeprefix('vision-')}{ext}"]
                (folder / "fixtures" / s["images"][0]).write_bytes(picture)
                (folder / "task.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if made >= args.count:
                break
        print(f"{name}: {made} {'would be ' if args.dry_run else ''}written")


if __name__ == "__main__":
    main()
