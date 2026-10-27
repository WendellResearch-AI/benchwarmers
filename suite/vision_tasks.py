"""Make the vision tasks: pictures drawn here, each with its answer key.

    python suite/vision_tasks.py          # (re)writes suite/tasks/vision-*/

Needs Pillow, to draw (`pip install pillow`); nothing else does. The suite
reads the PNGs and task.json files this writes, which are committed, and
suite/run.py copies them into the data folder (run.seed_shipped).

THE KEY EXISTS BY CONSTRUCTION, as with suite/seed.py: every number and name
in a picture comes from the same data its task's answer key is built from, so
the two cannot disagree. The pictures are made, not photographed: nobody's
receipt, card or screen, so they ship with the public repository. What they
test is reading -- printed text, digits that must be exact, a chart against
its gridlines, a table cell by row and column, a screen's state, counting,
a page turned on its side -- the jobs vision seats are given.

ONE FIELD IN EACH ASKS FOR SOMETHING THE PICTURE DOES NOT SHOW. The prompt
says to write `not shown` then; a value is an invention, scored like a trap
in the text suite. Reading an image confidently wrong is the failure that
costs, the same as asserting a bug that is not there.

Answers are lines, `key: value`. Scoring is the suite's own (run.score):
regexes over the lowered answer, tolerant of markdown around the key.
"""

from __future__ import annotations

import json
import math
import pathlib

from PIL import Image, ImageDraw, ImageFont

from vision_keys import NOT_SHOWN, PROMPT, clause

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "tasks"


def font(size: int, kind: str = "sans"):
    names = {"sans": ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
             "bold": ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
             "mono": ("consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf")}[kind]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def write(task_id: str, title: str, image: Image.Image, lines: list[str], find: list[dict],
          not_claim: list[dict], why: str) -> None:
    folder = OUT / task_id
    (folder / "fixtures").mkdir(parents=True, exist_ok=True)
    name = task_id.removeprefix("vision-") + ".png"
    image.save(folder / "fixtures" / name, optimize=True)
    spec = {"id": task_id, "title": title, "shape": "vision", "images": [name],
            "prompt": PROMPT.format(lines="\n".join(lines)), "min_answer_chars": 20,
            "must_find": find, "must_not_claim": not_claim, "why": why,
            "made_by": "suite/vision_tasks.py"}
    (folder / "task.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {folder.relative_to(HERE.parent)} ({len(find)} fields)")


# ---- a receipt: printed text, a date to rewrite, a sum to find ----------

def receipt() -> None:
    items = [("Oat milk 1L", 3.49), ("Sourdough loaf", 5.25), ("Blueberries 250g", 4.99), ("Cheddar 200g", 6.80)]
    subtotal = round(sum(p for _, p in items), 2)
    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + tax, 2)
    img = Image.new("RGB", (520, 760), "#f4f1ea")
    d = ImageDraw.Draw(img)
    mono, big = font(22, "mono"), font(30, "bold")
    d.text((260, 50), "HARBOR LANE GROCERY", font=big, fill="#222", anchor="mm")
    d.text((260, 92), "212 Quay Street", font=mono, fill="#333", anchor="mm")
    d.text((260, 122), "Mar 14, 2026   17:42", font=mono, fill="#333", anchor="mm")
    d.line((40, 160, 480, 160), fill="#888", width=2)
    y = 190
    for name, price in items:
        d.text((50, y), name, font=mono, fill="#222")
        d.text((470, y), f"{price:.2f}", font=mono, fill="#222", anchor="ra")
        y += 44
    d.line((40, y + 6, 480, y + 6), fill="#888", width=2)
    y += 30
    for label, value in (("SUBTOTAL", subtotal), ("TAX 8%", tax)):
        d.text((50, y), label, font=mono, fill="#222")
        d.text((470, y), f"{value:.2f}", font=mono, fill="#222", anchor="ra")
        y += 40
    d.text((50, y + 10), "TOTAL", font=big, fill="#111")
    d.text((470, y + 10), f"{total:.2f}", font=big, fill="#111", anchor="ra")
    d.text((260, y + 110), "PAID BY CARD", font=mono, fill="#333", anchor="mm")
    d.text((260, y + 150), "THANK YOU", font=mono, fill="#333", anchor="mm")
    priciest = max(items, key=lambda i: i[1])[0].split()[0].lower()
    write("vision-receipt", "Read a receipt", img,
          ["store: <name>", "date: <YYYY-MM-DD>", "items: <how many line items were bought>",
           "priciest: <the most expensive item>", "total: <amount paid>", "tip: <tip amount>"],
          [clause("store", r"harbor lane", "store: Harbor Lane Grocery"),
           clause("date", r"2026-03-14\b", "date: 2026-03-14"),
           clause("items", r"4\b", f"items: {len(items)}"),
           clause("priciest", rf"{priciest}", "priciest: Cheddar 200g"),
           clause("total", rf"\$?\s*{total:.2f}\b".replace(".", r"\."), f"total: {total:.2f}"),
           clause("tip", NOT_SHOWN, "tip: not shown")],
          [clause("tip", r"\$?\s*\d", "tip: a value (the receipt shows none)")],
          "Printed text, a date written differently from how it is asked for, a count and a maximum.")


# ---- a grading label: digits that must be exact -------------------------

def label() -> None:
    img = Image.new("RGB", (900, 300), "#dfe3e8")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 20, 880, 280), radius=14, fill="#ffffff", outline="#1d3557", width=4)
    d.rectangle((20, 20, 880, 70), fill="#1d3557")
    d.text((450, 45), "NORTHLINE GRADING", font=font(28, "bold"), fill="#ffffff", anchor="mm")
    sans, small = font(26, "bold"), font(20)
    d.text((50, 95), "2019 HARBOR SERIES", font=sans, fill="#111")
    d.text((50, 135), "#148  MAREN OKAFOR", font=sans, fill="#111")
    d.text((50, 175), "GOLD REFRACTOR", font=sans, fill="#111")
    d.text((50, 225), "CEN 9.5   COR 9   EDG 9.5   SUR 8.5", font=small, fill="#333")
    d.text((760, 110), "MINT", font=font(30, "bold"), fill="#111", anchor="mm")
    d.text((760, 175), "9", font=font(80, "bold"), fill="#b22222", anchor="mm")
    d.text((620, 240), "CERT 40731958", font=font(22, "mono"), fill="#111", anchor="mm")
    x = 520                                              # a barcode's look; it encodes nothing
    for i, w in enumerate((3, 1, 2, 1, 1, 3, 2, 1, 3, 1, 2, 2, 1, 3, 1, 1, 2, 3, 1, 2)):
        if i % 2 == 0:
            d.rectangle((x, 90, x + w * 2, 150), fill="#111")
        x += w * 2 + 2
    write("vision-label", "Read a graded card's label", img,
          ["grader: <company>", "grade: <the number>", "year: <year>", "number: <card number>",
           "player: <name>", "cert: <certification number>", "surface: <surface subgrade>",
           "autograph: <is the card signed: yes or no>"],
          [clause("grader", r"northline", "grader: Northline"),
           clause("grade", r"(mint\s*)?9\b(?!\.)", "grade: 9"),
           clause("year", r"2019\b", "year: 2019"),
           clause("number", r"#?\s*148\b", "number: 148"),
           clause("player", r"maren okafor", "player: Maren Okafor"),
           clause("cert", r"#?\s*40731958\b", "cert: 40731958"),
           clause("surface", r"8\.5\b", "surface: 8.5"),
           # A signed card's label says AUTO; this one does not, so "no" reads it right.
           clause("autograph", r"(no\b|" + NOT_SHOWN[1:], "autograph: no, or not shown")],
          [clause("autograph", r"(yes|signed|auto)", "autograph: yes (the label says nothing of one)")],
          "A label read field by field; the cert number must be exact to the digit.")


# ---- a bar chart: values against gridlines ------------------------------

def bars() -> None:
    teams = [("Billing", 45), ("Mobile", 25), ("Web", 35), ("Data", 15), ("Infra", 30)]
    img = Image.new("RGB", (820, 560), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((410, 35), "Support tickets by team, Q2", font=font(26, "bold"), fill="#111", anchor="mm")
    left, right, top, bottom, most = 90, 780, 80, 470, 50
    for v in range(0, most + 1, 5):
        y = bottom - (bottom - top) * v / most
        d.line((left, y, right, y), fill="#d0d0d0" if v % 10 else "#a8a8a8", width=1)
        if v % 10 == 0:
            d.text((left - 12, y), str(v), font=font(18), fill="#333", anchor="rm")
    d.line((left, top, left, bottom), fill="#333", width=2)
    d.line((left, bottom, right, bottom), fill="#333", width=2)
    slot = (right - left) / len(teams)
    for i, (name, v) in enumerate(teams):
        x0 = left + slot * i + slot * 0.2
        x1 = left + slot * (i + 1) - slot * 0.2
        d.rectangle((x0, bottom - (bottom - top) * v / most, x1, bottom - 1), fill="#3a6ea5")
        d.text(((x0 + x1) / 2, bottom + 22), name, font=font(20), fill="#111", anchor="mm")
    d.text((30, (top + bottom) / 2), "tickets", font=font(18), fill="#333", anchor="mm")
    tall, short = max(teams, key=lambda t: t[1])[0], min(teams, key=lambda t: t[1])[0]
    over = sum(v > 30 for _, v in teams)
    write("vision-bars", "Read a bar chart", img,
          ["tallest: <team>", "shortest: <team>", "web: <Web's value>",
           "over_30: <how many teams are above 30, not counting 30 itself>", "target: <the target line's value>"],
          [clause("tallest", tall.lower(), f"tallest: {tall}"),
           clause("shortest", short.lower(), f"shortest: {short}"),
           clause("web", r"35\b", "web: 35"),
           clause("over_30", rf"{over}\b", f"over_30: {over}"),
           clause("target", NOT_SHOWN, "target: not shown")],
          [clause("target", r"\d", "target: a value (the chart has no target line)")],
          "Values read against gridlines every 5, labelled every 10; one count asks for care at the boundary.")


# ---- a table: a cell by row and column ----------------------------------

def table() -> None:
    cols = ["Q1", "Q2", "Q3", "Q4"]
    rows = [("North", [1240, 1315, 1402, 1388]), ("South", [980, 1045, 1120, 1206]),
            ("East", [1510, 1470, 1535, 1602]), ("West", [870, 905, 948, 1011]),
            ("Central", [1105, 1160, 1098, 1175])]
    x0, y0, cw, rh, first = 40, 70, 150, 54, 180
    img = Image.new("RGB", (x0 * 2 + first + cw * len(cols), 420), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((img.width / 2, 32), "Units shipped by region", font=font(26, "bold"), fill="#111", anchor="mm")
    sans, bold = font(22), font(22, "bold")
    d.rectangle((x0, y0, x0 + first + cw * len(cols), y0 + rh), fill="#e8eef5")
    d.text((x0 + 14, y0 + rh / 2), "Region", font=bold, fill="#111", anchor="lm")
    for j, c in enumerate(cols):
        d.text((x0 + first + cw * (j + 1) - 16, y0 + rh / 2), c, font=bold, fill="#111", anchor="rm")
    for i, (name, vals) in enumerate(rows):
        y = y0 + rh * (i + 1)
        if i % 2:
            d.rectangle((x0, y, x0 + first + cw * len(cols), y + rh), fill="#f6f6f6")
        d.text((x0 + 14, y + rh / 2), name, font=sans, fill="#111", anchor="lm")
        for j, v in enumerate(vals):
            d.text((x0 + first + cw * (j + 1) - 16, y + rh / 2), f"{v:,}", font=sans, fill="#111", anchor="rm")
    d.rectangle((x0, y0, x0 + first + cw * len(cols), y0 + rh * (len(rows) + 1)), outline="#999", width=2)
    val = {name: dict(zip(cols, vals)) for name, vals in rows}
    q4 = max(rows, key=lambda r: r[1][3])[0]

    def num(n: int) -> str:
        s = f"{n:,}"
        return s.replace(",", ",?") + r"\b" if "," in s else s + r"\b"
    write("vision-table", "Read a table", img,
          ["south_q3: <number>", "west_q1: <number>", "central_q3: <number>",
           "q4_highest: <the region with the most units in Q4>", "northeast_q2: <number>"],
          [clause("south_q3", num(val["South"]["Q3"]), "south_q3: 1,120"),
           clause("west_q1", num(val["West"]["Q1"]), "west_q1: 870"),
           clause("central_q3", num(val["Central"]["Q3"]), "central_q3: 1,098"),
           clause("q4_highest", q4.lower(), f"q4_highest: {q4}"),
           clause("northeast_q2", NOT_SHOWN, "northeast_q2: not shown")],
          [clause("northeast_q2", r"\d", "northeast_q2: a value (there is no Northeast row)")],
          "Cells found by row and column; Central's Q3 dips, so a value from the wrong column shows.")


# ---- a settings screen: state, not just text -----------------------------

def screen() -> None:
    img = Image.new("RGB", (760, 640), "#e9ecef")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((30, 30, 730, 610), radius=12, fill="#ffffff", outline="#c8ccd0", width=2)
    d.text((60, 62), "Sync settings", font=font(28, "bold"), fill="#111")
    d.rounded_rectangle((60, 110, 700, 160), radius=8, fill="#fdecea", outline="#e0a39d")
    d.text((80, 135), "Last sync failed: storage quota exceeded (E-213)", font=font(20), fill="#8a1c12", anchor="lm")
    toggles = [("Sync on mobile data", False), ("Back up photos", True),
               ("Notify on conflicts", True), ("Pause when battery is low", False)]
    y = 195
    for name, on in toggles:
        d.text((60, y + 15), name, font=font(22), fill="#111", anchor="lm")
        d.rounded_rectangle((620, y, 690, y + 30), radius=15, fill="#2b7a3d" if on else "#c4c4c4")
        cx = 675 if on else 635
        d.ellipse((cx - 12, y + 3, cx + 12, y + 27), fill="#ffffff")
        y += 52
    d.text((60, y + 10), "When files conflict", font=font(22, "bold"), fill="#111")
    options, chosen = ["Prefer the newer file", "Keep both", "Prefer this device"], "Keep both"
    y += 50
    for o in options:
        d.ellipse((62, y, 84, y + 22), outline="#555", width=2)
        if o == chosen:
            d.ellipse((68, y + 6, 78, y + 16), fill="#1f5fbf")
        d.text((96, y + 11), o, font=font(21), fill="#111", anchor="lm")
        y += 36
    d.rounded_rectangle((420, 548, 550, 590), radius=8, fill="#eeeeee", outline="#d5d5d5")
    d.text((485, 569), "Sync now", font=font(20), fill="#a8a8a8", anchor="mm")
    d.rounded_rectangle((570, 548, 700, 590), radius=8, fill="#1f5fbf")
    d.text((635, 569), "Save", font=font(20, "bold"), fill="#ffffff", anchor="mm")
    write("vision-screen", "Read a settings screen", img,
          ["toggles_on: <every switch that is on>", "conflict: <the option chosen>",
           "disabled: <the button that cannot be pressed>", "error_code: <code>", "account: <the signed-in email>"],
          [clause("toggles_on", r".*back up photos", "toggles_on: Back up photos"),
           clause("toggles_on", r".*notify on conflicts", "toggles_on: Notify on conflicts"),
           clause("conflict", r"keep both", "conflict: Keep both"),
           clause("disabled", r"sync now", "disabled: Sync now"),
           clause("error_code", r"e-?213\b", "error_code: E-213"),
           clause("account", NOT_SHOWN, "account: not shown")],
          [clause("toggles_on", r".*(mobile data|battery)", "toggles_on: a switch that is off"),
           clause("account", r"\S+@\S+", "account: an email (the screen shows none)")],
          "A screen's state: which switches are on, which option is picked, which button is greyed out.")


# ---- shapes: counting and where things are -------------------------------

def shapes() -> None:
    img = Image.new("RGB", (800, 600), "#ffffff")
    d = ImageDraw.Draw(img)
    blue = [(110, 120), (250, 90), (180, 300), (390, 210), (120, 480), (330, 420)]
    red = [(520, 330), (640, 470), (260, 540)]
    green = [(470, 120), (560, 520)]
    for x, y in blue:
        d.ellipse((x - 34, y - 34, x + 34, y + 34), fill="#2f6fd0")
    for x, y in red:
        d.rectangle((x - 32, y - 32, x + 32, y + 32), fill="#d33a2f")
    for x, y in green:
        d.polygon([(x, y - 38), (x - 38, y + 30), (x + 38, y + 30)], fill="#2e9e4f")
    cx, cy, r = 715, 85, 50                              # the star, in the top-right corner
    d.polygon([(cx + (r if i % 2 == 0 else r * 0.45) * math.cos(math.radians(-90 + 36 * i)),
                cy + (r if i % 2 == 0 else r * 0.45) * math.sin(math.radians(-90 + 36 * i))) for i in range(10)],
              fill="#f2c200", outline="#b38f00")
    write("vision-shapes", "Count shapes and say where one is", img,
          ["blue_circles: <how many>", "red_squares: <how many>", "green_triangles: <how many>",
           "top_right: <the shape in the top-right corner, with its colour>", "purple_shapes: <how many>"],
          [clause("blue_circles", rf"{len(blue)}\b", f"blue_circles: {len(blue)}"),
           clause("red_squares", rf"{len(red)}\b", f"red_squares: {len(red)}"),
           clause("green_triangles", rf"{len(green)}\b", f"green_triangles: {len(green)}"),
           clause("top_right", r".*star", "top_right: a yellow star"),
           clause("purple_shapes", r"(0\b|none\b|zero\b|no\b|" + NOT_SHOWN[1:], "purple_shapes: 0")],
          [clause("purple_shapes", r"[1-9]", "purple_shapes: a count above 0 (there are none)")],
          "Counting to six without losing one, and a corner told apart from its neighbours.")


# ---- an invoice turned on its side ---------------------------------------

def invoice() -> None:
    page = Image.new("RGB", (640, 820), "#ffffff")
    d = ImageDraw.Draw(page)
    d.text((40, 40), "INVOICE", font=font(40, "bold"), fill="#111")
    d.text((40, 100), "INV-2026-0417", font=font(26, "mono"), fill="#111")
    d.text((40, 160), "Issued: 2026-04-02", font=font(22), fill="#333")
    d.text((40, 195), "Due: 2026-05-02", font=font(22), fill="#333")
    d.text((40, 260), "Bill to", font=font(20, "bold"), fill="#555")
    d.text((40, 290), "Kestrel Labs BV", font=font(24), fill="#111")
    d.text((40, 322), "Havenstraat 18, Rotterdam", font=font(20), fill="#333")
    lines = [("Model evaluation, March", 960.00), ("Hosting, March", 324.50)]
    y = 400
    d.line((40, y - 10, 600, y - 10), fill="#bbb", width=2)
    for name, amount in lines:
        d.text((40, y), name, font=font(22), fill="#111")
        d.text((600, y), f"EUR {amount:,.2f}", font=font(22), fill="#111", anchor="ra")
        y += 44
    d.line((40, y + 4, 600, y + 4), fill="#bbb", width=2)
    total = sum(a for _, a in lines)
    d.text((40, y + 30), "Amount due", font=font(26, "bold"), fill="#111")
    d.text((600, y + 30), f"EUR {total:,.2f}", font=font(26, "bold"), fill="#111", anchor="ra")
    img = page.rotate(90, expand=True, fillcolor="#ffffff")   # turned anticlockwise: reads bottom to top
    write("vision-invoice", "Read an invoice turned on its side", img,
          ["invoice: <invoice number>", "due: <YYYY-MM-DD>", "amount_due: <amount>", "bill_to: <company>",
           "po_number: <purchase order number>"],
          [clause("invoice", r"inv-2026-0417\b", "invoice: INV-2026-0417"),
           clause("due", r"2026-05-02\b", "due: 2026-05-02"),
           clause("amount_due", r"(eur|€)?\s*1,?284\.50\b", "amount_due: EUR 1,284.50"),
           clause("bill_to", r"kestrel labs", "bill_to: Kestrel Labs BV"),
           clause("po_number", NOT_SHOWN, "po_number: not shown")],
          [clause("po_number", r"\d", "po_number: a value (the invoice has none)")],
          "A page a scanner or a phone delivered sideways; two dates close together, only one asked for.")


if __name__ == "__main__":
    for make in (receipt, label, bars, table, screen, shapes, invoice):
        make()
