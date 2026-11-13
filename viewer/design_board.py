"""T30 "Next building": one inline SVG of the fictional PLATO-40 building with insights pinned to its parts.

Pure (no Streamlit). Geometry comes from building.json (floors, rooms N/S, gateways); which insight sits on which
part comes from design_board.toml; all text and numbers on cards come from insights.jsonl.
"""
from __future__ import annotations

import html
import re
try:
    import tomllib as _toml
except ModuleNotFoundError:  # Python < 3.11
    import tomli as _toml
from pathlib import Path

TOML = Path(__file__).with_name("design_board.toml")
W, H = 1000, 640
X0, X1 = 170, 830            # building body
GROUND, FLOOR_H = 600, 88    # ground line, storey height
CAR_TOP = 505                # underside of floor 1 (stilts below)
CONF = {"high": "#2e7d32", "medium": "#ef8f00", "low": "#c62828"}


def load_board(path: Path = TOML) -> dict:
    if not Path(path).exists():
        return {}
    with open(path, "rb") as f:
        return _toml.load(f)


def conf_level(c) -> str:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "low"
    return "high" if c >= 0.75 else "medium" if c >= 0.5 else "low"


def first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    m = re.search(r"(?<=[a-z0-9)%°])\.(?:\s|$)", text)
    return text[: m.start() + 1] if m else text


def pin_label(insight_id: str) -> str:
    """'PLATO-P3' -> '3', 'CLARA-C4' -> 'C4'."""
    tail = str(insight_id).split("-")[-1]
    return tail[1:] if tail.startswith("P") else tail


def floors(building: dict) -> list[dict]:
    """Per floor (bottom up): N rooms, S rooms, gateway ids."""
    b = building.get("building", building)
    n = int(b.get("floors") or max([r["floor"] for r in b.get("rooms", [])] or [0]))
    gws = {}
    for g in b.get("gateways", []):
        gws.setdefault(int(g.get("floor", 0)), []).append(g["id"])
    out = []
    for f in range(1, n + 1):
        rooms = sorted(r["id"] for r in b.get("rooms", []) if int(r["floor"]) == f)
        ori = {r["id"]: r.get("orientation") for r in b.get("rooms", [])}
        out.append({"floor": f, "N": [r for r in rooms if ori[r] == "N"], "S": [r for r in rooms if ori[r] != "N"],
                    "gateways": gws.get(f, [])})
    return out


def floor_y(i: int) -> tuple[int, int]:
    """(top, bottom) y of floor index i (0 = floor 1)."""
    bottom = CAR_TOP - i * FLOOR_H
    return bottom - FLOOR_H, bottom


def anchors(fl: list[dict]) -> dict[str, tuple[int, int]]:
    """Pin position per drawing element."""
    n = len(fl)
    roof = floor_y(n - 1)[0] if n else CAR_TOP
    up = floor_y(max(n - 2, 0))[0] if n else CAR_TOP
    return {"car_park": (300, 560), "floor1_slab": (360, CAR_TOP), "south_upper": (X1 + 28, up + 20),
            "roof_plant": (650, roof - 58), "roof_access": (565, roof - 30), "risers": (500, (roof + CAR_TOP) // 2),
            "technical_room": (500, 560), "gateways": (X0 + 26, (roof + CAR_TOP) // 2 - 30),
            "heat_pump": (640, 560), "rooms": (700, floor_y(0)[0] + 40 if n else CAR_TOP),
            "housekeeping": (250, floor_y(min(1, n - 1))[0] + 40 if n else CAR_TOP),
            "supplies": (760, 560)}


def pins(rows: list[dict], board: dict, layers=("PLATO",)) -> tuple[list[dict], list[str], list[str]]:
    """(pins to draw, insight ids without a pin, pinned ids not found / pointing at an unknown element)."""
    cfg = board.get("pins", {})
    elements = board.get("elements", {})
    by_id = {r.get("insight_id"): r for r in rows}
    out, unpinned, unknown = [], [], []
    for r in rows:
        iid, target = r.get("insight_id"), str(r.get("target", "")).upper()
        if target not in layers:
            continue
        c = cfg.get(iid)
        if not c or c.get("element") not in elements:
            unpinned.append(iid)
            continue
        out.append({"id": iid, "label": pin_label(iid), "target": target, "element": c["element"],
                    "element_name": elements[c["element"]], "incidents": list(c.get("incidents", [])),
                    "level": conf_level(r.get("confidence")), "topic": r.get("topic", "")})
    for iid, c in cfg.items():
        if iid.split("-")[0] in layers and (iid not in by_id or c.get("element") not in elements):
            unknown.append(iid)
    return out, unpinned, unknown


def _t(x, y, s, size=13, anchor="middle", fill="#37474f", weight="normal"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{fill}" '
            f'font-weight="{weight}" font-family="sans-serif">{html.escape(str(s))}</text>')


def svg(building: dict, pin_list: list[dict], board: dict | None = None, selected: str | None = None,
        href: bool = True) -> str:
    board = board or load_board()
    el = board.get("elements", {})
    fl = floors(building)
    n = len(fl)
    roof = floor_y(n - 1)[0] if n else CAR_TOP
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" '
         f'aria-label="PLATO-40 schematic section" style="max-width:1200px;background:#fbfaf7">']
    p.append('<defs><pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" '
             'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#b0bec5" stroke-width="2"/>'
             '</pattern></defs>')
    p.append(f'<rect x="0" y="{GROUND}" width="{W}" height="{H - GROUND}" fill="#e7e2d8"/>')
    p.append(f'<line x1="0" y1="{GROUND}" x2="{W}" y2="{GROUND}" stroke="#8d8577" stroke-width="2"/>')
    # car park under stilts
    for x in range(X0 + 10, X1, 110):
        p.append(f'<rect x="{x}" y="{CAR_TOP}" width="10" height="{GROUND - CAR_TOP}" fill="#9e9e9e"/>')
    for i, x in enumerate((215, 325, 655, 765)):
        p.append(f'<rect x="{x}" y="{GROUND - 26}" width="64" height="20" rx="7" fill="#90a4ae"/>'
                 f'<circle cx="{x + 14}" cy="{GROUND - 5}" r="6" fill="#455a64"/>'
                 f'<circle cx="{x + 50}" cy="{GROUND - 5}" r="6" fill="#455a64"/>')
    p.append(_t(300, GROUND + 22, el.get("car_park", "Car park"), 12))
    # technical room, heat pump, supplies at ground level
    p.append(f'<rect x="450" y="{CAR_TOP + 22}" width="100" height="{GROUND - CAR_TOP - 22}" fill="#cfd8dc" '
             f'stroke="#607d8b"/>')
    p.append(_t(500, GROUND + 22, "Technical room", 12))
    p.append(f'<rect x="600" y="{GROUND - 46}" width="44" height="40" rx="4" fill="#ffccbc" stroke="#bf6f52"/>')
    p.append(_t(622, GROUND + 22, el.get("heat_pump", "Heat pump"), 12))
    # floors
    for i, f in enumerate(fl):
        top, bot = floor_y(i)
        p.append(f'<rect x="{X0}" y="{top}" width="{X1 - X0}" height="{FLOOR_H}" fill="#fff" stroke="#546e7a" '
                 f'stroke-width="2"/>')
        p.append(_t(X0 - 12, (top + bot) // 2 + 4, f"Floor {f['floor']}", 12, "end"))
        for side, xs, xe in (("N", X0 + 60, 470), ("S", 530, X1 - 20)):
            k = len(f[side]) or 1
            w = (xe - xs) / k
            for j, room in enumerate(f[side]):
                x = xs + j * w
                p.append(f'<rect x="{x + 4:.0f}" y="{top + 22}" width="{w - 8:.0f}" height="{FLOOR_H - 44}" '
                         f'fill="#e3f2fd" stroke="#90a4ae"><title>Room {room[1:]}</title></rect>')
        gx, gy = X0 + 26, top + 22
        if f["gateways"]:
            p.append(f'<circle cx="{gx}" cy="{gy}" r="9" fill="#5c6bc0"><title>Sensor gateway</title></circle>'
                     f'<path d="M{gx - 6} {gy - 13} q6 -6 12 0" stroke="#5c6bc0" fill="none"/>')
        else:
            p.append(f'<circle cx="{gx}" cy="{gy}" r="9" fill="none" stroke="#9e9e9e" stroke-dasharray="3 2">'
                     f'<title>No gateway listed for this floor</title></circle>')
    if n:
        p.append(f'<rect x="{X0}" y="{CAR_TOP - 6}" width="{X1 - X0}" height="8" fill="url(#hatch)" '
                 f'stroke="#546e7a"/>')
        p.append(_t(250, 128 if roof > 160 else roof - 70, "North", 12, fill="#78909c"))
        p.append(_t(X1 - 80, 128 if roof > 160 else roof - 70, "South →", 12, fill="#78909c"))
        # risers
        for dx, col in ((-8, "#4fc3f7"), (8, "#ef9a9a")):
            p.append(f'<line x1="{500 + dx}" y1="{CAR_TOP + 22}" x2="{500 + dx}" y2="{roof}" stroke="{col}" '
                     f'stroke-width="5"/>')
        # roof: plant box, access ladder, PV
        p.append(f'<rect x="610" y="{roof - 44}" width="120" height="44" fill="#dcedc8" stroke="#689f38"/>')
        p.append(_t(670, roof - 18, "Ventilation unit", 12))
        for y in range(roof - 40, roof, 8):
            p.append(f'<line x1="582" y1="{y}" x2="596" y2="{y}" stroke="#795548" stroke-width="2"/>')
        p.append(f'<line x1="582" y1="{roof - 44}" x2="582" y2="{roof}" stroke="#795548" stroke-width="2"/>'
                 f'<line x1="596" y1="{roof - 44}" x2="596" y2="{roof}" stroke="#795548" stroke-width="2"/>')
        for x in range(220, 440, 50):
            p.append(f'<polygon points="{x},{roof} {x + 40},{roof} {x + 30},{roof - 16} {x - 10},{roof - 16}" '
                     f'fill="#1a237e" opacity="0.75"/>')
        p.append(_t(330, roof - 24, "Solar panels", 12))
    # pins
    pos = anchors(fl)
    seen: dict[str, int] = {}
    for pin in pin_list:
        x, y = pos.get(pin["element"], (W // 2, 60))
        k = seen.get(pin["element"], 0)
        seen[pin["element"]] = k + 1
        x += 30 * k
        colour = CONF[pin["level"]]
        ring = ' stroke="#212121" stroke-width="4"' if pin["id"] == selected else ' stroke="#fff" stroke-width="2"'
        shape = (f'<circle cx="{x}" cy="{y}" r="15" fill="{colour}"{ring}/>' if pin["target"] == "PLATO" else
                 f'<rect x="{x - 15}" y="{y - 13}" width="30" height="26" rx="6" fill="{colour}"{ring}/>')
        body = (shape + _t(x, y + 5, pin["label"], 13, fill="#fff", weight="bold")
                + f'<title>{html.escape(pin["topic"])} — {html.escape(pin["element_name"])}</title>')
        p.append(f'<a href="?pin={pin["id"]}" target="_self">{body}</a>' if href else f"<g>{body}</g>")
    p.append("</svg>")
    return "".join(p)
