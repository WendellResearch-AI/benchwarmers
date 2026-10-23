"""What a model is FOR, so the router does not seat a code-completion model to
review a design, a small instruct model to judge a large packet, or an image
model to enumerate call sites.

`config/model-roles.json` in the data folder gives each alias a ROLE under
`models` (an alias not listed is `general`) and lists `ad_hoc` aliases. Those
two are the install's own. The roles themselves and the task SHAPES that admit
them are the product's, so they are defined here and every install gets them;
until the split (RELEASE-PLAN Phase 1) all four lived in one personal file.

roles:   general  reasoning / do-it-all: eligible for every shape
         code     trained for code completion and edits; sharp on "find every
                  place X happens" and "is this function wrong", weak on
                  process, prose and design judgement
         light    small, fast instruct models (nano/3b/12b/30b-a3b class): short
                  bounded tasks (classify, extract, summarise, enumerate over a
                  small file); not review or design over a large packet
         image    image generation; never a reviewer
         vision   a specialist at reading images (OCR, documents, screenshots,
                  charts), weak at text-only work: admitted by the vision shape
                  alone, and a collection member with this role sits only when
                  the request carries images (gateway.py). A model that reads
                  images AND does everything else stays general.
         decision typed answers with probabilities through a Decisions API, not
                  chat; no shape admits them (config/decision-models.json)

shapes:  today's task, `route.py --shape <name>`; general when none is given.
         `quick` is a short, bounded task where a fast small model is right.
         `vision` is a question about images: the gateway gives it to a
         request that carries any, unless its collection sets a shape, and
         whatever the shape fills explorer seats only with models the catalog
         says take images (gateway.py). A role is what a model is for, not
         what it can read, so vision admits the roles that answer questions.

ad_hoc:  aliases NEVER chosen automatically: not by the router's seats or
         reserve, not by a benchmark sweep or `--next` rotation, not by the
         daily perf run. Orthogonal to role: a role says what a model is FOR,
         this says what it COSTS. They answer only when named: `--with` or
         the gateway's `with`, a collection that lists them as a member or
         merger (every request through it spends their quota), `llm -m`, a
         benchmark's `--model`. `bench onboard --quick --ad-hoc` (or Quick + ad
         hoc in the picker) onboards one this way from the start.

A role never lowers a score: rank is decided by evidence when seats are
picked; a role only decides which shapes a model is eligible for.
"""

from __future__ import annotations

import locked
from paths import DATA  # the install's data folder (metrics/paths.py)

FILE = DATA / "config" / "model-roles.json"

ROLES = {
    "general": "reasoning / do-it-all — eligible for every task shape",
    "code": "code completion and edits — eligible for review, code and enumeration;"
            " weak on design, process and prose",
    "light": "small, fast instruct model — eligible for quick and enumeration;"
             " not for review or design over a large packet",
    "image": "image generation — never a reviewer",
    "vision": "reads images (OCR, documents, screenshots) — sits only for requests that carry"
              " images; never a text reviewer",
    "decision": "typed choice / score / yes-no with probabilities, through a Decisions API"
                " — no chat, never a seat; see config/decision-models.json",
}
SHAPES = {
    "general": ["general"],
    "design": ["general"],
    "prose": ["general"],
    "review": ["general", "code"],
    "code": ["general", "code"],
    "enumeration": ["general", "code", "light"],
    "quick": ["general", "light"],
    "vision": ["general", "light", "vision"],
    "image": ["image"],
}


def load() -> dict:
    """{roles, shapes, models, ad_hoc}; {} parts when the file is missing.

    The data file's own `roles` and `shapes` win over the shipped ones, name by
    name. Files from before the split hold all four keys, with wording their
    owner may have tuned, and an upgrade must not silently replace it; a role
    or shape the product adds later still arrives, because only the names a
    file defines are overridden.
    ponytail: a file can redefine a shipped shape but not remove one; add a
    null-means-remove rule if anyone needs it."""
    data = locked.read_json(FILE)
    return {"roles": {**ROLES, **(data.get("roles") or {})},
            "shapes": {**SHAPES, **(data.get("shapes") or {})},
            "models": data.get("models") or {},
            "ad_hoc": data.get("ad_hoc") or []}
