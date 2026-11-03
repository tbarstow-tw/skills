---
name: system-drawing
description: Draws the system components involved in one change, question or research topic as a monochrome engineering sheet. The sheet has coded components, interfaces, flows, boundaries and budgets, and every item is backed by evidence from code, IaC, runtime data or docs. Use for "system drawing", "draw the systems involved in X", "how does X talk to Y", "blast radius of this change", or an architecture sheet for a design review. Not for UI components (use dimensional-drawing) or for whiteboard sketches with no evidence.
license: MIT. See LICENSE and THIRD_PARTY_NOTICES.md.
---

# System drawing

A system drawing answers one decision with one sheet. The sheet shows codes (C3, I2, T1). The tables and the register below the sheet hold names, values and evidence. Anything not found in a source is `[TBD]`. A wrong guess on a drawing is worse than a gap.

The kit builds the sheet from one YAML model. Never hand-edit the HTML. Change the model and rebuild.

## Files

- `schema/system-model.schema.json`: the model contract. Read it before you write a model.
- `kit/build.py <name>.model.yaml`: validates the model, runs the model checks, writes `<name>.html` beside it.
- `kit/check.py <name>.html`: renders at 1800 px, saves `<name>.png` beside it, runs the sheet checks.
- `kit/arimo.woff2`: the sheet font. `build.py` embeds it in each sheet, so text has the same width on every machine and the sheet needs no network.
- `kit/locators.py <name>.model.yaml --repo <source name>=<local clone>`: prints the lines behind each locator, read at the pinned SHA. Fails when a locator does not resolve.
- `examples/`: reference models and sheets. Copy the closest one. Do not start blank.

Requirements: Python 3 and git. Install the packages and the browser once:

```sh
python3 -m pip install pyyaml "jsonschema>=4" playwright
python3 -m playwright install chromium
```

Run the kit scripts by their path inside this skill folder. They find the schema, template and font from their own location.

## Procedure

1. **Name the subject and the decision.** Pick the mode:
   - `change`: a ticket, spec, plan or PR. Scope words CHANGE, TOUCH, CONTEXT, OUT.
   - `question`: how current behavior works. Scope words FOCUS, TOUCH, CONTEXT, OUT.
   - `research`: where a technology would fit. Same scope words as question.
   If you cannot name the decision the drawing supports, stop and ask.
2. **Pin the sources.** For each repository, use the default branch of the canonical remote at a recorded SHA. Run `git -C <repo> remote -v` first: the canonical remote points at the organization's repository. In a fork checkout that is often `upstream`, not `origin`. Fetch that remote, then read with `git show "<ref>:<path>"` and `git grep <pattern> <ref>`. Quote `<ref>:<path>`, because zsh reads `$R:s...` as a history modifier. Do not read working trees, which hold unrelated changes. Record the name, ref, SHA and date in `subject.sources`.
3. **Gather evidence.** Start at the subject's entry point (route, job, event, Terraform resource, or for a library the public API call that a caller makes). Walk one hop out in each direction. Walk a second hop only for TOUCH items. For each component, interface, boundary and budget, record a locator `<repo>@<sha7>:<path>:<lines>` and a confidence:
   - VERIFIED: read in source.
   - OBSERVED: seen in runtime data. State the time window.
   - DOCUMENTED: stated in a doc, not confirmed in code.
   - INFERRED: reasoned from names or structure. The drawing shows `?` after the code.
   - TBD: not found. The value is `[TBD]`.
   A doc comment (JSDoc, docstring) in a pinned source takes kind `doc`, its code locator, and DOCUMENTED, because it states intent, not behavior. A default that you read in a pinned source, including the library under study, takes a code locator and confidence VERIFIED. A default from a library or cloud service that is not in your sources takes a `doc` locator of the form `vendor:<product> <document title> (read YYYY-MM-DD)` and confidence DOCUMENTED. If you cannot find the default, it is TBD.
   For large subjects, split gathering across subagents: by repository, or in one large repository by module or by flow. Give each one the same output shape (components, interfaces, flows, data, boundaries, budgets, notes, unknowns) and merge the results yourself.
4. **Write the model.** Assign codes per drawing: `C#` component, `D#` data store, `X#` external or managed system, `I#` interface, `B#` boundary, `T#` duration (a timeout, an interval or a poll period), `L#` latency, `N#` count or limit, `M#` size. Put components in lanes (CLIENT, EDGE, SERVICE, ASYNC, DATA, EXTERNAL, PLATFORM). Keep at most 7 components per lane and 22 in total. Mark one flow as critical. It drives the step numbers on the plan and Detail A.
   - **Queues and topics:** draw one as a component (`D#`, kind `queue`, lane ASYNC) when its own settings matter: a dead-letter queue, a visibility timeout, a retry count, or fan-out to several subscribers. Otherwise draw it as an async edge and name it in `operation`.
   - **Off the plan:** `plan: false` on a component or interface keeps it in the model, the register and the evidence, but off the plan and the layer stack. Use it for CONTEXT leaves and for repeated edges, such as five functions that all read one database. Say what you left off in a note.
   - **Library subjects:** components are the runtime roles and stores that the library defines. One module can run in the caller and in the owner, so use lanes for roles: CLIENT for the caller role, SERVICE for the owner role, PLATFORM for transport and drivers, DATA for stores. If every data edge runs through one driver or client library, set `plan: false` on it and name it in a general note.
   - **No value:** when the source sets no timeout, write `timeout: none`. When it does not retry, write `retries: none`. When it retries with no cap, write `retries: unbounded`. Do not make a budget for a value that does not exist.
   - **Order:** component order inside a lane does not matter. The kit tries many orders and keeps the one with the shortest plan and the fewest crossings. Lane order is yours.
5. **Build.** Run `python3 kit/build.py <model.yaml>`. Fix every failure and rebuild.
6. **Check.** Run `python3 kit/check.py <sheet.html>`. Fix every failure with the remedies in [Legibility rules](#legibility-rules), in order. Then look at the PNG. The checks catch geometry, not meaning, so read the sheet once as a reviewer would.
7. **Reread the evidence.** Run `python3 kit/locators.py <model.yaml> --repo <name>=<path>` for each source. Read the printed lines for each tag and make sure that they hold the fact. Fix each locator that fails or points at the wrong lines, then rebuild.
8. **Deliver.** Save the model, HTML and PNG together in the owning project or research folder. Reply in 1-2 sentences: what the sheet shows, and which `[TBD]` items block the decision.

## Sheet grammar

- **Title block (HTML):** kicker, title, question, decision, drawing number `SYS-<AREA>-<nnn>`, revision, mode, scope, sources with SHAs, general notes, and 5-7 key figures. A key figure names the budget code that holds its value. When no budget holds it, for example an interface with `timeout: none`, it names that interface or component code.
- **Notes (top left):** numbered facts a reviewer must know. Each note has evidence. A note with `at:` also puts its number box inside that component's box. Two or more notes on one component stack left to right. A note with `confidence: INFERRED` shows `?` after its number. Do not write "(inferred)" in the note text.
- **Context plan (top center):** components in lanes, edges with interface codes. Critical-flow edges are heavier and carry step numbers. Interfaces with the same caller, callee and style share one drawn edge labeled with all their codes (`I11–13`). Boundaries with two or more members are frames: dash-dot for trust and network, thin solid for account, deploy and team. Boundaries with the same members share one frame labeled with all their codes. A one-member boundary shows as a tag after the scope word on its box. A frame that would enclose a non-member is not drawn; the tables and register keep it.
- **Layer stack (bottom left):** the lanes lifted apart, to show which layers the subject crosses.
- **Detail A (bottom center):** the sequence of `views.sequence_flow`. Each participant box shows its code and name. When a name does not fit in two lines, every box shows its code only, so that all boxes read the same way. The plan and the Components table give the names. A step label is the interface code and its timeout code. A label that crosses the next lifeline moves to the first free gap on its row. Above about 9 participants a label can find no gap, and `check.py` fails.
- **Detail B (bottom right):** `failure` shows timeout, retries, failure behavior, gate and auth for `views.failure_interface`. `blast_radius` shows E1 direct, E2 transitive and E3 user-visible effects (change mode).
- **Tables (right):** Components, Interfaces, Boundaries, Budgets, in code order. Columns: Code, Name, Value, Ev (first evidence tag). They list what the sheet draws: components and interfaces on the plan or in a detail, every boundary, and each budget whose code appears on the drawing, in a note or in a key figure. Everything else stays in the register. The right column holds about 58 rows. `build.py` fails early when the tables would not fit.
- **Register (HTML below the sheet):** sources, full component and interface tables (operation, auth, gate, failure), flows, boundaries, budgets, unknowns, and the evidence register. This is the part a reviewer audits.

## Line semantics

- Component outline: heavy for CHANGE or FOCUS, normal for TOUCH, thin for CONTEXT. Hatched for OUT. Dashed for TBD.
- Fill: light gray for data stores and queues, darker gray for managed and vendor systems.
- Edges: solid with a filled arrow for a sync call. Dashed with an open arrow for an async message. Solid with a dot at the store end for data access.

## House rules

- Monochrome. Kinds differ by weight, dash, hatch and fill.
- Responsibilities and contracts, not code. No code excerpts, SQL or config bodies.
- Codes on the drawing, values in tables. No bare "30 s" on the drawing.
- No secrets, customer data, hostnames, IP addresses, account IDs, ARNs or URLs anywhere in the model. Use logical names. `build.py` rejects common patterns. It is not a complete scan, so read the model before you deliver it.
- Never invent a value. A budget with a `[TBD]` input has a `[TBD]` result.
- One subject per drawing. Split when the subject spans more than one decision.
- A sheet is a snapshot. The SHAs and date make staleness visible. Rebuild from fresh sources before you reuse it.

## Legibility rules

The sheet must read on the first pass. `check.py` enforces these rules and fails the sheet when one breaks.

Text:
- No truncated text. The kit never shortens a label; text that does not fit fails.
- No text overlaps other text.
- No text crosses a line, an edge, a frame or a box border. Text inside a box or table cell keeps 2 px clear of its side edges and stays inside its top and bottom. An edge label may sit on its own edge only.

Edges (context plan):
- Parallel segments of two edges run at least 8 px apart. The kit routes on 10 px tracks, and runs that do not overlap share a track.
- No edge runs within 6 px of a boundary frame line.
- Edge ends on one box sit at least 12 px apart.
- No edge passes through a box other than its own two ends, or through the gap between two side-connected boxes.
- An edge bends at most 4 times.
- More crossings than edges prints a warning.

Space:
- Gutters grow to fit their tracks. The plan may use all height that Detail A and Detail B do not need at their minimum step (22 px per sequence step).
- If the plan, a detail, the layer stack or a box side does not fit, the subject is too dense.

Remedies, in this order:
1. Shorten the label in the model. Table values (`protocol`, budget `value`) are short words such as `HTTPS` or `STS SDK`; put detail in `operation`. Component names stay under about 18 characters.
2. Change the lane order. The kit already searches component order inside each lane, so do not reorder components by hand.
3. Remove a note, or shorten the critical flow in Detail A. A Detail A label failure means too many participants: start the flow at a later step, or leave out a CONTEXT hop.
4. Set `plan: false` on CONTEXT leaf components (one edge, no finding) and on repeated edges, such as many components that read one store. They stay in the register. Name them in a note.
5. Split the subject into two drawings that share codes.

Never relax a rule's number to make a sheet pass.

## Model checks (build.py)

Schema, unique codes, lanes, scope words that match the mode, references between items, one critical flow whose steps are on the plan, evidence on every non-TBD item, every evidence tag used, notes numbered 1..n and placed on plan components, formulas whose inputs exist, table space, sensitive-data patterns, and characters that the embedded font cannot show (Latin, punctuation and arrows only; no emoji or CJK). `build.py` does not open the repositories, so it cannot confirm that a locator's lines hold the fact. `locators.py` prints those lines. Read them before you deliver.

## Sheet checks (check.py)

Page errors, the embedded font loaded, the legibility rules above, labels in the sheet margin, bare values with units outside tables (notes count as drawing text, so write `T3`, not `55 s`), tables that run into the margin, and colors outside the gray tokens.

Adapted from the dimensional-drawing skill by Enes Ozturk (MIT). The sheet font is Arimo (OFL). See `THIRD_PARTY_NOTICES.md`.
