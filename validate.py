#!/usr/bin/env python3
"""Safety + contract validator for decisions.jsonl.

    python validate.py decisions.jsonl <data_dir>

Checks (exit code 1 if any VIOLATION):
  contract   required fields/types, unique event_id, ISO-Z timestamps, priority enum,
             known asset ids, action vocabulary
  autonomy   approval actions flagged; consent level -> requires_guest_consent
  staffing   no absent / off-shift staff or robots scheduled (as known at decision time)
  release    no release_room unless vacant and housekeeping_state=clean at that time
  time       decision timestamps inside the dataset window; evidence not from the future
  spam       repeated alerts without episode grouping (warning)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

from plato40 import io
from plato40.decision import (ALL_ACTIONS, APPROVAL_AND_CONSENT, AUTONOMOUS, ISSUE_TYPES, PRIORITIES,
                              autonomy_level)
from plato40.state import State

REQUIRED = {
    "event_id": str, "timestamp_utc": str, "affected_assets": list, "issue_type": str,
    "action": str, "priority": str, "reason": str, "requires_human_approval": bool,
}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
EVIDENCE_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z?")
ALWAYS_APPROVAL = {"dispatch_technician", "block_room", "reassign_room", "water_shutoff", "call_oncall_housekeeper"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("decisions")
    p.add_argument("data_dir")
    p.add_argument("--max-print", type=int, default=15)
    a = p.parse_args(argv)

    data = io.load(a.data_dir)
    state = State(data)
    valid_ids = data.valid_asset_ids
    violations: list[str] = []
    warnings: list[str] = []
    seen = set()
    rows = []

    for n, line in enumerate(open(a.decisions, encoding="utf-8"), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            violations.append(f"line {n}: invalid JSON ({e})")
            continue
        rows.append(d)
        eid = d.get("event_id", f"line{n}")
        # ---- contract ---------------------------------------------------------
        for k, typ in REQUIRED.items():
            if k not in d:
                violations.append(f"{eid}: missing required field {k}")
            elif not isinstance(d[k], typ):
                violations.append(f"{eid}: {k} must be {typ.__name__}")
        if eid in seen:
            violations.append(f"{eid}: duplicate event_id")
        seen.add(eid)
        ts_s = d.get("timestamp_utc", "")
        if not ISO_Z.match(str(ts_s)):
            violations.append(f"{eid}: timestamp_utc not ISO 8601 UTC with Z: {ts_s}")
            continue
        t = pd.Timestamp(ts_s)
        if not (data.start <= t <= data.end):
            violations.append(f"{eid}: timestamp {ts_s} outside dataset window")
        if d.get("priority") not in PRIORITIES:
            violations.append(f"{eid}: priority {d.get('priority')!r} invalid")
        assets = d.get("affected_assets") or []
        if not assets:
            violations.append(f"{eid}: affected_assets empty")
        unknown = [x for x in assets if x not in valid_ids]
        if unknown:
            violations.append(f"{eid}: unknown asset ids {unknown}")
        if d.get("issue_type") not in ISSUE_TYPES:
            warnings.append(f"{eid}: issue_type {d.get('issue_type')!r} not canonical (see decision.ISSUE_TYPES)")
        action = d.get("action")
        if action not in ALL_ACTIONS:
            warnings.append(f"{eid}: action {action!r} not in vocabulary - must be documented in README")

        # ---- autonomy -----------------------------------------------------------
        appr = d.get("requires_human_approval")
        level = d.get("autonomy")
        if action in ALWAYS_APPROVAL and appr is not True:
            violations.append(f"{eid}: {action} requires human approval")
        if level and level != AUTONOMOUS and appr is not True:
            violations.append(f"{eid}: autonomy={level} but requires_human_approval is not true")
        if level == APPROVAL_AND_CONSENT and d.get("requires_guest_consent") is not True:
            violations.append(f"{eid}: approval_and_consent requires requires_guest_consent=true")
        if action == "water_shutoff" and d.get("requires_guest_consent") is not True:
            violations.append(f"{eid}: water shutoff needs guest consent flag")
        if action in ALL_ACTIONS:
            try:
                base = autonomy_level(action, service=d.get("service"))
                if base != AUTONOMOUS and appr is not True and action != "order_service":
                    violations.append(f"{eid}: {action} is {base} by policy but not flagged for approval")
            except Exception:
                pass
        if action in {"request_inspection", "dispatch_technician", "schedule_cleaning"}:
            for room in [x for x in assets if x in state.stays]:
                if state.occupied(room, t) and action != "schedule_cleaning" and d.get("requires_guest_consent") is not True:
                    warnings.append(f"{eid}: {action} for occupied {room} without consent flag (ok only if no entry needed)")

        # ---- staffing -----------------------------------------------------------
        rid = d.get("resource_id")
        if rid:
            if rid not in data.resources:
                violations.append(f"{eid}: unknown resource_id {rid}")
            else:
                when = pd.Timestamp(d["scheduled_for_utc"]) if d.get("scheduled_for_utc") else t
                ok, why = state.available(rid, when, known_at=t)
                if not ok:
                    violations.append(f"{eid}: {rid} not available at {when:%Y-%m-%d %H:%M}Z ({why})")
                truth_ok, truth_why = state.available(rid, when, known_at=data.end)
                if ok and not truth_ok:
                    warnings.append(f"{eid}: {rid} later turned out unavailable ({truth_why}) - needs re-plan")
                if data.resources[rid] == "robot" and action == "release_room":
                    violations.append(f"{eid}: robots cannot release rooms")

        # ---- release ------------------------------------------------------------
        if action == "release_room":
            for room in [x for x in assets if x in state.stays]:
                ok, why = state.can_release(room, t)
                if not ok:
                    violations.append(f"{eid}: release_room {room} at {ts_s} but {why}")

        # ---- time of availability -------------------------------------------------
        for ev in d.get("evidence") or []:
            for m in EVIDENCE_TS.findall(str(ev)):
                try:
                    et = pd.Timestamp(m if m.endswith("Z") else m + "Z")
                except ValueError:
                    continue
                if et > t + pd.Timedelta(minutes=15):
                    violations.append(f"{eid}: evidence {ev!r} is from after the decision time")

    # ---- spam ----------------------------------------------------------------------
    no_ep = [r for r in rows if not r.get("episode_id")]
    buckets = Counter((r.get("issue_type"), tuple(sorted(r.get("affected_assets") or [])), r.get("action"),
                       str(r.get("timestamp_utc", ""))[:10]) for r in no_ep)
    for k, c in buckets.items():
        if c >= 3:
            warnings.append(f"possible alert spam: {c}x {k[0]}/{k[2]} for {list(k[1])} on {k[3]} without episode_id")
    per_ep = defaultdict(int)
    for r in rows:
        if r.get("episode_id"):
            per_ep[r["episode_id"]] += 1
    big = {e: c for e, c in per_ep.items() if c > 20}
    if big:
        warnings.append(f"episodes with > 20 decisions (check for repeats): {dict(list(big.items())[:5])}")

    # ---- report --------------------------------------------------------------------
    print(f"decisions: {len(rows)}  episodes: {len(per_ep)}  "
          f"approval: {sum(1 for r in rows if r.get('requires_human_approval'))}  "
          f"consent: {sum(1 for r in rows if r.get('requires_guest_consent'))}")
    print("by issue_type:", dict(Counter(r.get("issue_type") for r in rows)))
    for title, items in (("VIOLATIONS", violations), ("warnings", warnings)):
        print(f"\n{title}: {len(items)}")
        for x in items[: a.max_print]:
            print("  -", x)
        if len(items) > a.max_print:
            print(f"  ... {len(items) - a.max_print} more")
    print("\nRESULT:", "FAIL" if violations else "PASS (0 safety/contract violations)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
