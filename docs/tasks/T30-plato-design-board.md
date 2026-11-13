# T30 — PLATO design board: lessons pinned to the building

**Branch:** feature branch from `extras`, PR into `extras` (see `docs/EXTRAS.md`) · **Priority:** P1 ·
**Viewer only** · **Score impact:** evidence-based feedback to CLARA and PLATO (10), target architecture (5), demo (8)

## Goal
Turn the Level 3 PLATO recommendations into a picture a builder reads in seconds: a simple side view of the fictional
PLATO-40 building on stilts above the car park, with each lesson pinned to the physical part it concerns. Clicking a pin
shows the evidence, the recommendation, the expected benefit (estimate) and the uncertainty.

## Data
`insights.jsonl` (use `viewer.views.common.insights_dir()`): `insight_id, target, topic, finding, evidence,
recommendation, expected_benefit, confidence, status, uncertainty`. `building.json`: 4 floors, 40 rooms (orientation
N/S, area), assets HP1, AHU1, PV1, gateways GW_F1–GW_F3 (note: no gateway listed for floor 4), technical room.

Pin mapping lives in an editable file `viewer/design_board.toml` (no mapping in code):
| Insight | Element on the drawing |
|---|---|
| PLATO-P1 Floor above the car park runs cold | floor-1 slab over the car park |
| PLATO-P2 Summer overheating without cooling | south façade, upper floors |
| PLATO-P3 Rooftop plant needs permanent access | roof plant (AHU1) + access |
| PLATO-P4 Sub-meter risers and the technical room | vertical risers + technical room / main water meter |
| PLATO-P5 Redundant sensor gateways per floor | one gateway icon per floor (floor 4 empty) |
Optional layer "CLARA (operations)": C1–C6 pinned to rooms/plant (C4 filter → AHU1, C3 pre-heat → HP1, …).

## Do
1. `viewer/design_board.py`: builds one **SVG** (inline, no images, scales to width): car park with parked cars under
   stilts, 4 floors × 10 room windows (N and S side as two faces or a simple section), roof with plant box, risers,
   technical room, gateway icons per floor; pins as numbered circles coloured by confidence (high / medium / low),
   positions from `design_board.toml`. Labels in plain words; "PLATO-40 (fictional)" in the caption.
2. `viewer/views/design.py`, page **"Next building"** (router entry after What we learned, icon `:material/architecture:`):
   - The drawing on top; toggle "Show operations lessons (CLARA)" adds the second layer.
   - Clicking a pin (or its number in the list below) opens a card: topic, first sentence of the finding with its
     number, recommendation, expected benefit labelled *estimate, not measured*, confidence chip, status
     (confirmed / qualified), "show full evidence" expander, link to the related incidents (e.g. P3 → the AHU filter
     incident) via `?episode=`.
   - Under the drawing: the five PLATO lessons as a compact numbered list matching the pins.
3. One link line at the top of *What we learned*: "See the lessons on the building →".

## Acceptance criteria
- All 5 PLATO insights appear as pins at the right element; missing or unknown ids are listed, never crash.
- Numbers shown come from `insights.jsonl` text only; changing the file changes the page.
- Works with missing insights (friendly empty state + the generate button from #40).
- AppTest on quickstart, dev, empty decisions; a unit test checks every PLATO insight has a pin in the toml.
- Readable in the demo at 1920×1080 and on a laptop screen; PR with screenshots (with and without the CLARA layer).

## Files to touch
`viewer/design_board.py`, `viewer/design_board.toml`, `viewer/views/design.py`, `viewer/app.py` (one router line),
`viewer/views/learned.py` (one link line), `tests/test_viewer_design.py`.
