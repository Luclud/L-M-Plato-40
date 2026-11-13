#!/usr/bin/env python3
"""Robustness check: run the pipeline on trimmed copies of a dataset (different start, shorter length).

    python scripts/robustness_check.py                       # data/dev -> out/robustness/
    python scripts/robustness_check.py --data data/dev --out out/robustness --keep

Variants (relative to the dataset): start 1 Mar, start 1 Jul (to the end), first 60 days, first 21 days.
Every time-indexed file is filtered to the window and the manifest is rewritten; stays open at the new start
get `stay_in_progress` events (as the organiser data does at its own start). For each variant: run.py +
validate.py, reporting crashes, violations, runtime and decisions per agent. Holdout period/profile is
unknown, so the pipeline must not assume January or 365 days.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# file -> time column used to cut the window (rows with start <= t < end are kept)
POINT_FILES = {
    "access_log.csv": "timestamp_utc", "energy.csv": "timestamp_utc", "equipment.csv": "timestamp_utc",
    "logistics.csv": "timestamp_utc", "room_telemetry.csv": "timestamp_utc", "room_water.csv": "timestamp_utc",
    "water_main.csv": "timestamp_utc", "weather.csv": "timestamp_utc", "tariff.csv": "timestamp_utc",
    "weather_forecast.csv": "issued_utc", "guest_requests.csv": "timestamp_utc", "work_orders.csv": "created_utc",
}
# file -> (start column, end column): rows overlapping the window are kept
SPAN_FILES = {"bookings.csv": ("arrival_utc", "departure_utc"), "staff_roster.csv": ("start_utc", "end_utc")}
STAY_START = {"checkin", "checkin_early", "checkin_after_wait", "stay_in_progress"}
STAY_END = {"checkout", "checkout_early"}


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.replace("", None), utc=True, format="ISO8601")


def _iso(t: pd.Timestamp) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def variants(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    y = start.year
    out = {}
    for name, (m, d) in {"start_1mar": (3, 1), "start_1jul": (7, 1)}.items():
        s = pd.Timestamp(year=y, month=m, day=d, tz="UTC")
        if s <= start:
            s = pd.Timestamp(year=y + 1, month=m, day=d, tz="UTC")
        if s < end - pd.Timedelta(days=7):
            out[name] = (s, end)
    for days in (60, 21):
        e = start + pd.Timedelta(days=days)
        if e < end:
            out[f"first_{days}d"] = (start, e)
    return out


def trim(src: Path, dst: Path, start: pd.Timestamp, end: pd.Timestamp, name: str) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file() and f.name not in POINT_FILES and f.name not in SPAN_FILES and f.name not in (
                "events.jsonl", "manifest.json"):
            shutil.copy2(f, dst / f.name)
    for fname, col in POINT_FILES.items():
        if (src / fname).exists():
            df = pd.read_csv(src / fname, dtype=str, keep_default_na=False)
            t = _ts(df[col])
            df[(t >= start) & (t < end)].to_csv(dst / fname, index=False)
    for fname, (a, b) in SPAN_FILES.items():
        if (src / fname).exists():
            df = pd.read_csv(src / fname, dtype=str, keep_default_na=False)
            ta, tb = _ts(df[a]), _ts(df[b])
            df[(tb > start) & (ta < end)].to_csv(dst / fname, index=False)
    # events: keep the window; stays open at the new start become stay_in_progress at start
    rows = [json.loads(line) for line in (src / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    open_: dict[str, dict] = {}
    kept = []
    for r in rows:
        t = pd.Timestamp(r["timestamp_utc"])
        if t < start:
            if r.get("type") in STAY_START and r.get("room_id"):
                open_[r["room_id"]] = r
            elif r.get("type") in STAY_END and r.get("room_id"):
                open_.pop(r["room_id"], None)
        elif t < end:
            kept.append(r)
    sip = [{"event_id": f"ESIP{i:05d}", "timestamp_utc": _iso(start), "type": "stay_in_progress",
            "room_id": r["room_id"], "booking_id": r.get("booking_id"), "guests": r.get("guests")}
           for i, r in enumerate(sorted(open_.values(), key=lambda x: x["room_id"]))] if start > pd.Timestamp(
        json.loads((src / "manifest.json").read_text())["start_utc"]) else []
    with (dst / "events.jsonl").open("w", encoding="utf-8") as f:
        for r in sip + kept:
            f.write(json.dumps({k: v for k, v in r.items() if v is not None}, ensure_ascii=False) + "\n")
    man = json.loads((src / "manifest.json").read_text())
    man.update(start_utc=_iso(start), end_utc=_iso(end), days=int(round((end - start) / pd.Timedelta(days=1))),
               profile=f"{man.get('profile', 'data')}-{name}",
               dataset_version=f"{man.get('dataset_version', '')}+trim-{name}")
    (dst / "manifest.json").write_text(json.dumps(man, indent=2))


def run_variant(d: Path) -> dict:
    out = d / "decisions.jsonl"
    t0 = time.time()
    p = subprocess.run([sys.executable, "run.py", str(d), "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    runtime = time.time() - t0
    res = {"variant": d.name, "run_rc": p.returncode, "runtime_s": round(runtime, 1)}
    if p.returncode != 0:
        res["error"] = (p.stderr or p.stdout).strip().splitlines()[-5:]
        return res
    v = subprocess.run([sys.executable, "validate.py", str(out), str(d)], cwd=ROOT, capture_output=True, text=True)
    res["validate"] = next((ln for ln in v.stdout.splitlines() if ln.startswith("RESULT")), v.stdout.strip()[-200:])
    res["validate_rc"] = v.returncode
    ds = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    res["decisions"] = len(ds)
    res["by_agent"] = dict(sorted(Counter(x.get("agent") or "?" for x in ds).items()))
    res["episodes"] = len({x.get("episode_id") or x["event_id"] for x in ds})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev")
    ap.add_argument("--out", default="out/robustness")
    ap.add_argument("--only", nargs="*", help="variant names to run")
    ap.add_argument("--keep", action="store_true", help="keep the trimmed data copies")
    ap.add_argument("--reference", help="decisions.jsonl of the full run: per-agent counts in the same window "
                                        "are shown as trimmed/full (warm-up effects)")
    a = ap.parse_args(argv)
    src, out = (ROOT / a.data).resolve(), (ROOT / a.out).resolve()
    man = json.loads((src / "manifest.json").read_text())
    vs = variants(pd.Timestamp(man["start_utc"]), pd.Timestamp(man["end_utc"]))
    ref = []
    if a.reference:
        ref = [json.loads(line) for line in Path(a.reference).read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for name, (s, e) in vs.items():
        if a.only and name not in a.only:
            continue
        d = out / name
        trim(src, d, s, e, name)
        r = run_variant(d)
        r["window"] = f"{_iso(s)} .. {_iso(e)}"
        if ref:
            r["reference_by_agent"] = dict(Counter(x.get("agent") or "?" for x in ref
                                                   if s <= pd.Timestamp(x["timestamp_utc"]) < e))
        results.append(r)
        print(f"[{name}] {r['window']} run rc={r['run_rc']} {r['runtime_s']} s  "
              f"{r.get('validate', 'CRASH')}  decisions={r.get('decisions', '-')}", flush=True)
        if not a.keep:
            for f in d.iterdir():
                if f.name not in ("decisions.jsonl",):
                    f.unlink()
    agents = sorted({k for r in results for k in list(r.get("by_agent", {})) + list(r.get("reference_by_agent", {}))})

    def cell(r, k):
        n = r.get("by_agent", {}).get(k, 0)
        return f"{n}/{r['reference_by_agent'].get(k, 0)}" if "reference_by_agent" in r else str(n)
    lines = ["| variant | window (UTC) | run | runtime s | validate | decisions | " + " | ".join(agents) + " |",
             "|---|---|---|---:|---|---:|" + "---:|" * len(agents)]
    for r in results:
        lines.append(f"| {r['variant']} | {r['window']} | {'ok' if r['run_rc'] == 0 else 'CRASH'} | {r['runtime_s']} | "
                     f"{r.get('validate', '-')} | {r.get('decisions', '-')} | "
                     + " | ".join(cell(r, k) for k in agents) + " |")
    if ref:
        lines.append("\nAgent columns: decisions in the trimmed run / decisions of the full run in the same window.")
    report = "\n".join(lines)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report + "\n")
    (out / "report.json").write_text(json.dumps(results, indent=2, default=str))
    print("\n" + report)
    bad = [r for r in results if r["run_rc"] != 0 or r.get("validate_rc", 1) != 0]
    for r in bad:
        print(f"\nFAILED {r['variant']}: {r.get('error') or r.get('validate')}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
