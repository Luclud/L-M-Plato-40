"""Helpers for writing detectors (domain agents)."""
from __future__ import annotations

import heapq
import itertools
from typing import Any

import pandas as pd

from ..engine import Context, Detector
from ..io import Data
from ..state import State


class TriggerDetector(Detector):
    """Pattern: precompute causal triggers in build_triggers(), handle them at their time.

    A trigger at time ts must be computable from data <= ts (backward-looking windows only).
    handle() is called on the first clock tick >= ts, with ctx.t = that tick, so decisions
    are stamped with the moment the system could have acted.
    """

    def setup(self, data: Data, state: State) -> None:
        self._q: list[tuple[pd.Timestamp, int, str, Any]] = []
        self._seq = itertools.count()
        for ts, kind, payload in self.build_triggers(data, state):
            heapq.heappush(self._q, (ts, next(self._seq), kind, payload))

    def build_triggers(self, data: Data, state: State):
        return []

    def step(self, ctx: Context) -> None:
        while self._q and self._q[0][0] <= ctx.t:
            ts, _, kind, payload = heapq.heappop(self._q)
            self.handle(ctx, ts, kind, payload)

    def handle(self, ctx: Context, ts: pd.Timestamp, kind: str, payload: Any) -> None:
        raise NotImplementedError


def run_starts(mask: pd.Series, min_len: int) -> tuple[pd.Series, pd.Series]:
    """For a boolean series indexed by time: causal streak length, plus
    (times where the streak reaches min_len, times where a qualifying streak ends)."""
    m = mask.fillna(False).astype(bool)
    grp = (~m).cumsum()
    streak = m.groupby(grp).cumsum()
    hits = streak.index[streak == min_len]
    # end = first False after a streak that reached min_len
    reached = streak.groupby(grp).transform("max") >= min_len
    ends = m.index[(~m) & (m.shift(1, fill_value=False)) & reached.shift(1, fill_value=False)]
    return pd.Series(hits), pd.Series(ends)


def floor_of(room_id: str) -> int:
    return int(room_id[1])
