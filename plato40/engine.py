"""Replay engine.

The clock steps through the dataset (default 15 min). At each step every detector
("domain agent") gets a Context with the current time t and as-of views; it may
add decisions to the shared log. Detectors may precompute *causal* features in
setup() (rolling windows that only look backwards), but must only emit a decision
at the time the triggering information was available.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

from .asof import AsOf
from .io import Data
from .log import DecisionLog
from .state import State


@dataclass
class Context:
    data: Data
    state: State
    log: DecisionLog
    t: pd.Timestamp
    step: pd.Timedelta

    @property
    def asof(self) -> AsOf:
        return AsOf(self.data, self.t)

    @property
    def prev(self) -> pd.Timestamp:
        return self.t - self.step


class Detector:
    """Base class for a domain agent. Override setup() and/or step()."""

    name = "detector"

    def setup(self, data: Data, state: State) -> None:  # precompute causal features
        pass

    def step(self, ctx: Context) -> None:  # called at every clock tick
        pass

    def finish(self, ctx: Context) -> None:  # end of replay
        pass


def run(data: Data, detectors: list[Detector], step_minutes: int = 15, verbose: bool = True) -> DecisionLog:
    state = State(data)
    log = DecisionLog()
    step = pd.Timedelta(minutes=step_minutes)
    timings = {}
    for det in detectors:
        t0 = time.perf_counter()
        det.setup(data, state)
        timings[det.name] = time.perf_counter() - t0

    clock = pd.date_range(data.start, data.end, freq=step, inclusive="left")
    ctx = Context(data, state, log, clock[0], step)
    for t in clock:
        ctx.t = t
        for det in detectors:
            det.step(ctx)
    for det in detectors:
        det.finish(ctx)
    if verbose:
        print(f"[engine] {len(clock)} steps, setup times: " + ", ".join(f"{k}={v:.1f}s" for k, v in timings.items()))
    return log
