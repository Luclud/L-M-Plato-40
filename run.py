#!/usr/bin/env python3
"""Single documented command (CHALLENGE §8):

    python run.py <data_dir> [--out decisions.jsonl]

Reads a PLATO-40 data directory, replays it, writes decisions.jsonl and a
data-quality report next to it. Runs fully offline; an LLM is optional
(set LLM keys in .env, see .env.example) and only used for wording.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from plato40 import __version__, engine, io, llm
from plato40.detectors import REGISTRY, build


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="PLATO-40 autonomous building pipeline")
    p.add_argument("data_dir", help="dataset directory (contains manifest.json) or its parent")
    p.add_argument("--out", default="decisions.jsonl", help="output decisions file")
    p.add_argument("--step", type=int, default=15, help="replay step in minutes (default 15)")
    p.add_argument("--detectors", default="", help=f"comma list, default all: {','.join(REGISTRY)}")
    p.add_argument("--no-llm", action="store_true", help="force template wording (no LLM calls)")
    a = p.parse_args(argv)

    if a.no_llm:
        llm.set_enabled(False)
    print(f"[llm] {'enabled (wording only, template fallback)' if llm.enabled() else 'off - template wording'}")

    t0 = time.perf_counter()
    data = io.load(a.data_dir)
    print(f"[load] {data.manifest['profile']} {data.start:%Y-%m-%d}..{data.end:%Y-%m-%d} "
          f"in {time.perf_counter() - t0:.1f}s, {len(data.dq)} data-quality findings")

    detectors = build([d for d in a.detectors.split(",") if d] or None)
    log = engine.run(data, detectors, step_minutes=a.step)
    out = log.write(a.out)

    dq_path = out.with_name(out.stem + "_dq.json")
    dq_path.write_text(json.dumps([asdict(x) for x in data.dq], indent=1, default=str))
    summary = {"version": __version__, "profile": data.manifest["profile"], **log.summary(),
               "runtime_s": round(time.perf_counter() - t0, 1)}
    print(json.dumps(summary, indent=1))
    print(f"[done] wrote {out} and {dq_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
