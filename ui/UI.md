# The dashboard's vocabulary

What the dashboard is made of, so a page is composed from pieces that exist
and a new one looks like its neighbours. `serve.py` writes the HTML,
`ui/bench.css` holds everything here, `ui/bench.js` wires it. No framework, no
build step.

## Rules

- **Quiet.** A neutral frame, hairlines, no shadow on anything at rest, no
  frosted glass, no cards inside cards.
- **One accent, for where you are**: the page marked in the rail, the row in
  hand, the item selected in the palette. Nothing else is tinted. States keep
  their own colours (ok, warn, err).
- **A theme sets colours only** (`ui/themes/*.css`). Every colour in
  `bench.css` is a theme token or a `color-mix` of tokens, so all themes keep
  working. `scripts/check_themes.py` holds each theme to WCAG AA.
- **The whole width is used**, from a phone to a 3440 px screen. Prose keeps a
  measure of 70ch inside whatever holds it; tables, lists and charts take the
  room.
- **A record opens beside its list**, in the docked panel, not in a popup.
  Popups (`<dialog>`) are for a confirm, a picker and a short create.
- **Motion shows where you are or what an action did.** Never data, never
  waiting. All of it is off under `prefers-reduced-motion`.

## Tokens (`:root` in bench.css)

| Token | Value | For |
|---|---|---|
| `--t-small` | .75rem (12px) | small labels, counts; nothing is smaller |
| `--t-body` | .875rem (14px) | the interface: tables, controls, panel text |
| `--t-lead` | 1rem (16px) | a page's name, a section's heading |
| `--t-head` | 1.25rem (20px) | a popup's heading |
| weights | 400, 500, 700 | text, labels and controls, headings (800: the wordmark) |
| `--gap-1` … `--gap-5` | 4, 8, 12, 16, 24px | every gap and padding |
| `--pad` | clamp(14px, 1.1vw, 28px) | a pane's side padding |
| `--rail-w` | 232px | the rail |
| `--aside-w` | 400px at 1440 to 560px at 3440 | the docked panel |
| `--bar-h` | 48px | a pane's top bar |
| `--radius` | 4px (the theme's) | controls; chips are fully round; panes are square |
| `--line` | the theme's | every hairline; `--field` (a stronger mix) edges a control |
| `--mark`, `--tint` | the brand; the signal at 30% | the accent: a 3px bar and its wash |
| `--hover` | band mixed into paper | a row or control under the pointer |
| `--fast`, `--base`, `--slow` | 120, 180, 240ms | motion; `--ease` is cubic-bezier(.2, .7, .2, 1) |

Model ids, keys and figures are set in the mono face with tabular figures.
An unknown value is a muted dash and never shouts.

## The frame

Three panes that scroll on their own, under one line across the top (the
theme's court line):

- **The rail** (`nav.tabs`): the name on its bench, Search (the palette), the
  pages in their groups as links (each page has its own address), the marker,
  and at its foot the onboarding queue, Onboard a model and the theme. In
  Hardwood the planks show behind the rail and nowhere else, and its inner
  edge is the court's sideline (`--sideline`, a colour only a theme with a
  floor sets; the others draw `--line`).
- **The page** (`section.tab`): a bar (`.bar`: the page's name, its search or
  filter, a `.spring`, its own actions at the right; `serve.BAR_TOOLS`, fixed
  HTML that a page fetched again never replaces), then its sections.
- **The docked panel** (`aside.facts`, or the record in hand, `dialog.dock`:
  the model editor, a collection, an endpoint, a connection's quota): what the page holds in facts,
  the contents of a long page, or the record in hand with its actions at a
  fixed foot. With nothing in hand it says what it is for. A record is a
  `<dialog>` opened by its `open` attribute (not `showModal`), so the page
  beside it stays live; Escape and Close put it away and its row loses the mark.

Under 1280px the panel is a sheet from the right, shown when something is in
hand or the page's bar asks for it (Benchmarks' "On this page"). Under 800px
the rail is a bar with a menu.

## Pieces

- **Section** (`.panel`): a heading (`--t-lead`, bold), one sentence under it,
  then content. Sections are parted by a hairline, not boxed. A section with
  its own controls has a heading line (`.panel > header`: the heading, its
  controls, Share at the right). What explains it beyond the sentence is in a
  fold under it ("How this is measured").
- **Columns of sections** (`.cols`): sections flow in as many columns as fit,
  each kept whole (Benchmarks' reports: one on a laptop, three at 3440).
  Without script: `.cols.flow`, newspaper columns of 34rem, for sections of
  any height (Settings, Action needed); `.cols.fit`, a row the few groups
  share when each has `--fit` (26rem, Info; 52rem, Quota's tables).
- **Table**: spans its pane; hairline rows; a sticky head; numbers right in
  tabular figures, dates on one line (`td.when`); a heading sorts on click and
  shows its arrow; the row in hand carries the accent. A column nothing is
  known in, in any row, is left out; an unknown is a quiet dash. A group of
  rows is a `<tbody>` under a heading row whose button folds it (Models'
  connections; a sort orders each group), with one Collapse all / Expand all
  in the page's bar.
- **A long table in its own box** (`.list`, Models, Discover): it scrolls by
  itself under its sticky head, and sideways on a phone; an `opt` column
  leaves a narrow box (the docked panel has it), a `wide` one joins a wide one.
- **A list to act on** (`ul.items`, Action needed): each item its name and
  state, its actions at the right, its reason under them; past eight, the
  rest fold (`details.more`).
- **A list beside a detail** (`.split`): the things down the left, the chosen
  one beside it (Clients' apps, Logs' logs), each scrolling by itself so the
  page does not; the one chosen is the list's button with `aria-pressed`,
  its `aria-controls` naming what it shows. Under 800px the list sits above.
- **Facts** (`dl.rows`): a label and its value on a line, the labels in one
  column. A value the environment sets names its variable under it
  (`.set-by`), as Settings shows each setting read at start.
- **A long list as data** (Discover): the rows arrive as JSON in the page and
  only those shown are drawn, more as the list's foot nears; its filter,
  facets and sort work on the data. For a list of thousands, not dozens.
- **Chip** (`.chip`, with `ok`, `warn`, `err`, `new`, `adhoc`, `via`): a state
  or a mark, never a button unless it is one.
- **Buttons**: flat. `.btn` (an edge), `.btn.primary` (solid ink, one a view),
  `.btn.danger`, `.linkish` (reads as a link). A button whose action finishes
  says so on itself: a drawn tick and "Saved" or "Copied" for a second and a
  half.
- **Empty state** (`serve.empty_state`): the bench with its seats empty, one
  sentence, the action that fills it. On a fresh install that action is first
  run's next step, and Benchmarks' place holds first run itself: what
  Benchwarmers is, that step's one button, and the steps as a list that ticks
  off.
- **Palette** (Search in the rail, Ctrl+K): one field and what it finds, in
  groups (the last opened through it, pages, models, collections); the arrow
  keys choose, Enter opens. A model or a collection opens docked on its page.

## Motion

| What | Where |
|---|---|
| The marker slides to the page chosen | the rail |
| A tick is drawn when a step finishes | onboarding's and first run's steps, Save, Copy |
| The palette arrives in groups, recents first | Ctrl+K |
| The empty bench answers its button | first run, an empty page |
| The selection glides between rows; the arrow keys move it | a table in its own box with a row in hand; the keys on Models |
| A popup rises as it opens | every `<dialog>` and sheet |
| Rows glide to their new order | after a sort |
| A fold opens and closes smoothly | every `<details>` |
| The queue chip fills as models finish | the rail's foot; it never shakes |

Pages swap at once: there is no fade between them.

## Checking a change

    python scripts/check.py --quick      # ends with "all checks pass"
    python scripts/check_themes.py       # every theme, WCAG AA

and look at it at 390, 1440 and 3440px wide in every theme.
