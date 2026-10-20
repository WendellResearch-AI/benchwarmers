"""Repoint a connection's models after Open WebUI gains or changes a prefix.

    python scripts/reprefix.py llm7-api --prefix "llm7."
    python scripts/reprefix.py llm7-api --prefix "llm7." --apply

Open WebUI can prefix a connection's model ids, which is the fix for two
connections serving the same name — `codestral-latest` exists on both mistral
and llm7, and Open WebUI showed only one of them until llm7 got a prefix.

**THE RENAME IS CHEAP BECAUSE NOTHING HERE IS KEYED BY THE SERVED NAME.**
Every score, every perf row, every offload and every floor result is keyed by
the ALIAS. So repointing a connection is one line per model in the yaml, and
all history survives untouched — which is the answer to "will I have to redo
everything connected to the models". No.

Two things do need care, and this handles both:

1. **The availability snapshot.** It records served names, so after a prefix
   the old ones simply vanish and the daily check reports every model on the
   connection as retired. The snapshot is migrated rather than left to raise a
   page of false alarms.
2. **Aliases stay put.** It is tempting to rename `minimax-m2.7` to
   `llm7-minimax-m2.7` for tidiness. Do not: that orphans its perf history and
   its suite outputs, and the router would treat it as a model it has never
   seen. The alias is the identity; the served name is just an address.

Dry-run by default. `--apply` writes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "metrics"))
from paths import DATA  # noqa: E402  -- the install's data folder (metrics/paths.py)
YAML = DATA / "config" / "extra-openai-models.yaml"
SNAPSHOT = DATA / "config" / "served-snapshot.json"
TAGS = DATA / "config" / "provider-tags.json"


def registered() -> list[tuple[str, str]]:
    """(alias, served_name) in file order."""
    out, current = [], None
    for line in YAML.read_text(encoding="utf-8").splitlines():
        if (m := re.match(r"\s*-?\s*model_id:\s*(.+)", line)):
            current = m.group(1).strip().strip("\"'")
        elif (m := re.match(r"\s*model_name:\s*(.+)", line)):
            out.append((current, m.group(1).strip().strip("\"'")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("connection", help="tag as shown by provider_tags.py")
    ap.add_argument("--prefix", required=True,
                    help='what Open WebUI now prepends, e.g. "llm7."')
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    tags = json.loads(TAGS.read_text(encoding="utf-8"))
    served_now = set(tags)

    changes = []
    for alias, served in registered():
        if tags.get(served, "").split("/")[0] == args.connection:
            continue                               # already correct
        candidate = args.prefix + served
        # Only repoint when the PREFIXED name is what the endpoint now serves
        # and the bare one is not. Anything else is a different problem and
        # guessing at it would break a working registration.
        if candidate in served_now and served not in served_now:
            changes.append((alias, served, candidate))

    if not changes:
        print(f"Nothing to repoint for {args.connection}.")
        print("Either the prefix is already applied, or the endpoint does not "
              "serve the prefixed names.")
        return

    print(f"{len(changes)} registration(s) to repoint "
          f"({'applying' if args.apply else 'dry run'}):\n")
    for alias, old, new in changes:
        print(f"  {alias:<32} {old}  ->  {new}")
    print("\nAliases are NOT changed, so every score, perf row, offload and "
          "floor result\nkeeps pointing at the same model. That is why this "
          "is cheap.")

    if not args.apply:
        print("\nRe-run with --apply to write.")
        return

    text = YAML.read_text(encoding="utf-8")
    for _alias, old, new in changes:
        quoted_new = f'"{new}"' if any(c in new for c in ":/") else new
        text = re.sub(rf"^(  model_name: ){re.escape(old)}\"?$",
                      rf"\g<1>{quoted_new}", text, count=1, flags=re.M)
        text = re.sub(rf'^(  model_name: )"{re.escape(old)}"$',
                      rf"\g<1>{quoted_new}", text, count=1, flags=re.M)
    YAML.write_text(text, encoding="utf-8")

    # The snapshot records served NAMES, so without this the daily check
    # reports the whole connection as retired tomorrow morning.
    if SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        moved = 0
        for _alias, old, new in changes:
            if old in snap.get("last_seen", {}):
                snap["last_seen"][new] = snap["last_seen"].pop(old)
                moved += 1
            snap.get("first_missing", {}).pop(old, None)
        SNAPSHOT.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"\nmigrated {moved} snapshot entr"
              f"{'ies' if moved != 1 else 'y'} so the daily check does not "
              f"report them retired")

    print("\nNow run:  python scripts/sync_config.py && "
          "python scripts/provider_tags.py")


if __name__ == "__main__":
    main()
