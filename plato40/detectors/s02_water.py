"""S02 The Hidden Water Leak.

Three causal detectors on the cleaned meter data (io.py: valid readings only, resets -> NaN,
jitter -> 0, rate_l_per_interval spreads use over gaps):

- loud:   per room, sustained high flow (>= LOUD_L per 15 min) that is not cleaning. Showers/baths
          are bursty (<= 5 intervals in a year of dev data); a burst hose runs for hours.
          Vacant room -> trigger after 1 h, occupied room -> after 2 h.
- subtle: per room, continuous non-zero flow without a single zero interval for >= 6 h (running
          toilet ~1 L/15 min), or >= 2 consecutive local nights (01-05) almost always non-zero while
          the room's own night history (expanding median, >= 3 nights warm-up) is ~zero.
          Noisy meters after an exchange alternate 0 / small values and are therefore not continuous.
- gap:    night median of main - sum(rooms) vs a rolling baseline of previous non-incident nights;
          >= 2 consecutive nights above it -> leak outside the room meters (risers, technical room).
          Median (not sum) so intermittent irrigation/common-area use does not trigger.

Look-alikes ruled out: occupied + bursty usage, cleaning in progress (housekeeping_state), meter
reset/exchange (+-2 h, and known metering work orders), single-interval spikes, legionella flushing
(work orders known at t), post-exchange meter noise. Ladder per episode (`water:<room>` / `water:gap`):
flag_anomaly -> request_inspection (approval; consent if occupied, + notify_guest proposal) ->
water_shutoff for loud leaks still running 1 h later (approval + consent) / dispatch_technician when a
guest reports a water issue -> verification + close when flow returns to baseline.
"""
from __future__ import annotations

import heapq
import re

import numpy as np
import pandas as pd

from ..clock import at_local, iso, local
from ..decision import make_decision
from ..messaging import compose_guest_message
from .base import TriggerDetector

INTERVAL = pd.Timedelta(minutes=15)
LOUD_L = 8.0                # physical floor, litres per 15 min
LOUD_VACANT_N = 4           # 1 h continuous high flow in a vacant room
LOUD_N = 8                  # 2 h continuous high flow regardless of occupancy
LOUD_END_N = 4              # 1 h below the floor -> loud flow stopped
SHUTOFF_AFTER = pd.Timedelta(hours=1)
SUBTLE_N = 24               # 6 h without a zero interval
SUBTLE_CONFIRM_N = 48       # 12 h -> request inspection
SUBTLE_END_N = 8            # 2 h of zero flow -> back to baseline
NAN_BRIDGE = 2              # missing readings bridged inside a streak (unknown, not zero)
RESET_GUARD = pd.Timedelta(hours=2)
NIGHT = ("01:00", "05:00")  # local, DST-aware via clock.at_local
NIGHT_MIN_VALID = 12        # of 16 intervals
NIGHT_SHARE = 0.875         # >= 14 of 16 night intervals non-zero
NIGHT_BASE_MAX = 0.25
NIGHT_WARMUP = 3
GAP_MIN_L = 2.0             # litres per 15 min above baseline (= 8 L/h continuous)
GAP_MAD_K = 5.0
GAP_HISTORY = 14            # nights in the rolling gap baseline
GAP_NIGHTS = 2
LEGIONELLA_WINDOW = (pd.Timedelta(hours=-1), pd.Timedelta(hours=6))
ROOM_RE = re.compile(r"\bR\d{3}\b")


def _streak(m: np.ndarray) -> np.ndarray:
    """Causal run length of True values ending at each index (column-wise for 2-D)."""
    m = m.astype(bool)
    c = np.cumsum(m, axis=0)
    base = np.maximum.accumulate(np.where(~m, c, 0), axis=0)
    return c - base


def _bridge(cond: pd.DataFrame, valid: pd.DataFrame) -> np.ndarray:
    """cond on valid readings; short gaps inherit the previous state (unknown is not zero)."""
    return (cond.astype(float).where(valid).ffill(limit=NAN_BRIDGE).fillna(0.0) > 0.5).to_numpy()


def _end_after(run: np.ndarray, end_streak: np.ndarray, min_run: int, end_n: int) -> list[int]:
    """Indices where end_streak reaches end_n for the first time after a run that reached min_run."""
    out, armed = [], False
    hits = set(np.flatnonzero(end_streak == end_n))
    starts = np.flatnonzero(run == min_run)
    if not len(starts):
        return out
    nxt = 0
    for i in sorted(hits | set(starts)):
        if nxt < len(starts) and i == starts[nxt]:
            armed = True
            nxt += 1
        if i in hits and armed and run[i] == 0:
            out.append(i)
            armed = False
    return out


class WaterAgent(TriggerDetector):
    name = "water"

    # ------------------------------------------------------------------ features
    def build_triggers(self, data, state):
        self.ruled_out: list[dict] = []
        self._ep: dict[str, dict] = {}
        grid = pd.date_range(data.start, data.end, freq="15min", inclusive="left")
        self.grid = grid
        rooms = list(data.room_ids)
        rw = data.room_water.pivot_table(index="timestamp_utc", columns="room_id", values="rate_l_per_interval",
                                         aggfunc="first").reindex(index=grid, columns=rooms)
        self.rate = rw
        valid = rw.notna()

        blocked = pd.DataFrame(False, index=grid, columns=rooms)
        for row in data.room_water[data.room_water.reset].itertuples(index=False):
            if row.room_id in blocked.columns:
                blocked.loc[row.timestamp_utc:row.timestamp_utc + RESET_GUARD, row.room_id] = True
        bl = blocked.to_numpy()

        tel = data.telemetry.dropna(subset=["housekeeping_state"])
        hk = tel.pivot_table(index="timestamp_utc", columns="room_id", values="housekeeping_state", aggfunc="first")
        hk = hk.reindex(index=grid.union(hk.index)).ffill().reindex(index=grid, columns=rooms)
        cleaning = (hk == "in_progress").to_numpy()

        high = _bridge(rw >= LOUD_L, valid) & ~cleaning & ~bl
        nonzero = _bridge(rw > 0, valid) & ~bl
        hs, ns = _streak(high), _streak(nonzero)
        low = _streak(valid.to_numpy() & ~(rw >= LOUD_L).to_numpy())
        zero = _streak(valid.to_numpy() & (rw == 0).to_numpy())

        for j, room in enumerate(rooms):
            for i in np.flatnonzero(hs[:, j] == LOUD_VACANT_N):
                yield grid[i], "loud_candidate", {"room": room, "start": grid[i - LOUD_VACANT_N + 1]}
            for i in np.flatnonzero(hs[:, j] == LOUD_N):
                yield grid[i], "loud", {"room": room, "start": grid[i - LOUD_N + 1]}
            for i in _end_after(hs[:, j], low[:, j], LOUD_VACANT_N, LOUD_END_N):
                yield grid[i], "loud_end", {"room": room}
            for i in np.flatnonzero(ns[:, j] == SUBTLE_N):
                yield grid[i], "subtle", {"room": room, "start": grid[i - SUBTLE_N + 1]}
            for i in np.flatnonzero(ns[:, j] == SUBTLE_CONFIRM_N):
                yield grid[i], "subtle_confirm", {"room": room, "start": grid[i - SUBTLE_CONFIRM_N + 1]}
            for i in _end_after(ns[:, j], zero[:, j], SUBTLE_N, SUBTLE_END_N):
                yield grid[i], "subtle_end", {"room": room}

        yield from self._night_triggers(data, rw, valid, blocked, grid)
        yield from self._gap_triggers(data, rw, grid)

        gr = data.guest_requests
        for row in gr[gr.request_type == "water_issue"].itertuples(index=False):
            yield row.timestamp_utc, "guest_water", {"room": row.room_id, "request_id": row.request_id}

    def _nights(self, grid):
        lt = grid.tz_convert("Europe/Vienna")
        mins = lt.hour * 60 + lt.minute
        a, b = (int(x[:2]) * 60 + int(x[3:]) for x in NIGHT)
        mask = (mins >= a) & (mins < b)
        label = pd.Index(lt.date)
        # night is complete (and decidable) at its local end time; only nights fully inside the data
        ends = {d: at_local(pd.Timestamp(d).tz_localize("Europe/Vienna"), NIGHT[1]) for d in set(label[mask])}
        last = grid[-1] + INTERVAL
        return mask, label, {d: e for d, e in ends.items() if e <= last}

    def _night_triggers(self, data, rw, valid, blocked, grid):
        mask, label, ends = self._nights(grid)
        nz = (rw > 0).where(valid)[mask]
        lab = label[mask]
        share = nz.groupby(lab).mean()
        cnt = valid[mask].groupby(lab).sum()
        blk = blocked[mask].groupby(lab).any()
        share = share.where((cnt >= NIGHT_MIN_VALID) & ~blk)
        share = share.loc[[d for d in share.index if d in ends]]
        base = share.expanding(min_periods=NIGHT_WARMUP).median().shift(1)
        hit = (share >= NIGHT_SHARE) & (base <= NIGHT_BASE_MAX)
        two = hit & hit.shift(1, fill_value=False)
        for room in two.columns:
            for d in two.index[two[room].to_numpy()]:
                yield ends[d], "subtle_night", {"room": room, "night": str(d), "share": float(share.at[d, room]),
                                                "base": float(base.at[d, room])}

    def _gap_triggers(self, data, rw, grid):
        main = data.water_main.set_index("timestamp_utc")["rate_l_per_interval"]
        main = main[~main.index.duplicated()].reindex(grid)
        gap = main - rw.sum(axis=1, min_count=rw.shape[1])
        self.gap = gap
        mask, label, ends = self._nights(grid)
        g = gap[mask]
        med = g.groupby(label[mask]).median()
        cnt = g.notna().groupby(label[mask]).sum()
        hist: list[float] = []
        run, in_ep, frozen = 0, False, None
        for d in sorted(ends):
            if d not in med.index or cnt.get(d, 0) < NIGHT_MIN_VALID or pd.isna(med[d]):
                continue  # unknown night: neither normal nor leak
            v = float(med[d])
            ref = frozen if in_ep else self._gap_ref(hist)
            if ref is None:
                hist.append(v)
                continue
            b, thr = ref
            if v - b >= thr:
                run += 1
                if not in_ep and run >= GAP_NIGHTS:
                    in_ep, frozen = True, ref
                    yield ends[d], "gap", {"night": str(d), "excess": v - b, "base": b, "nights": run}
                elif in_ep and run == GAP_NIGHTS + 1:
                    yield ends[d], "gap_confirm", {"night": str(d), "excess": v - b, "base": b, "nights": run}
                if not in_ep:
                    continue  # suspicious night stays out of the baseline
            else:
                if in_ep and v - b < thr / 2:
                    in_ep, frozen = False, None
                    yield ends[d], "gap_end", {"night": str(d), "excess": v - b, "base": b}
                run = 0
                if not in_ep:
                    hist.append(v)

    @staticmethod
    def _gap_ref(hist):
        if len(hist) < NIGHT_WARMUP:
            return None
        h = np.array(hist[-GAP_HISTORY:])
        b = float(np.median(h))
        mad = float(np.median(np.abs(h - b))) * 1.4826
        return b, max(GAP_MIN_L, GAP_MAD_K * mad)

    # ------------------------------------------------------------------ helpers
    def _later(self, ts, kind, payload):
        heapq.heappush(self._q, (ts, next(self._seq), kind, payload))

    def _suppressed(self, ctx, room) -> str | None:
        wo = ctx.asof.work_orders()
        if wo.empty:
            return None
        met = wo[(wo.type == "metering") & (wo.asset_id == f"WM_{room}")]
        if ((met.scheduled_utc - ctx.t).abs() <= RESET_GUARD).any():
            return f"meter exchange {met.iloc[-1].work_order_id} scheduled within 2 h"
        leg = wo[wo.summary.str.contains("legionella", case=False, na=False)]
        for w in leg.itertuples(index=False):
            if room in ROOM_RE.findall(str(w.summary)) and pd.notna(w.scheduled_utc) and \
                    w.scheduled_utc + LEGIONELLA_WINDOW[0] <= ctx.t <= w.scheduled_utc + LEGIONELLA_WINDOW[1]:
                return f"legionella flush {w.work_order_id}"
        return None

    def _recent(self, room, t, start):
        s = self.rate[room].loc[start:t].dropna()
        return (float(s.mean()) if len(s) else float("nan")), (s.index[0] if len(s) else start)

    def _language(self, ctx, room):
        stay = ctx.state.stay_at(room, ctx.t)
        if stay is None:
            return None
        gr = ctx.asof.guest_requests()
        g = gr[gr.booking_id == stay.booking_id]
        return g.language.iloc[-1] if not g.empty else None

    def _bms_clue(self, ctx) -> list[str]:
        ev = ctx.asof.events(since=ctx.t - pd.Timedelta(hours=24))
        ev = ev[(ev.type == "bms_alarm") & (ev.alarm_code == "WATER_NIGHT_FLOW")]
        return [f"events.jsonl:{r.asset_id}:{iso(r.timestamp_utc)}:BMS WATER_NIGHT_FLOW (clue only)"
                for r in ev.itertuples(index=False)]

    def _add(self, ctx, key, **kw):
        kw.setdefault("agent", self.name)
        kw["evidence"] = list(kw.get("evidence") or [])
        return ctx.log.add(make_decision(t=ctx.t, issue_type="water_leak", **kw), episode_key=key, prefix="WL")

    def _inspect(self, ctx, room, key, why, evidence, priority, gr_ids=()):
        occ = ctx.state.occupied(room, ctx.t)
        ep = self._ep[key]
        ep["inspected"] = True
        self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="request_inspection", priority=priority,
                  reason=f"{why}. Inspect bathroom fittings (flexible hoses, toilet fill valve)"
                         + ("; room is occupied -> entry needs guest consent" if occ else "; room vacant"),
                  enters_occupied_room=occ, evidence=evidence, guest_request_ids=list(gr_ids),
                  expected_outcome="Source of the flow found and repaired; meter returns to its baseline",
                  verification_plan=f"WM_{room} flow back to baseline (zero between uses) within 1 h after repair; "
                                    "escalate if flow increases or a guest reports a water issue",
                  confidence=ep["conf"])
        if occ:
            lt = local(ctx.t + pd.Timedelta(hours=1))
            window = f"{lt:%H:%M}-{(lt + pd.Timedelta(hours=1)):%H:%M}"
            msg = compose_guest_message("maintenance_consent_request", self._language(ctx, room), room_id=room,
                                        reason="possible water leak in the bathroom", proposed_window_local=window)
            self._add(ctx, key, affected_assets=[room], action="notify_guest", priority=priority,
                      reason=f"Ask the guest in {room} for consent to a short technician visit (suspected leak)",
                      message=msg, evidence=evidence,
                      expected_outcome="Guest consent (or refusal) recorded before entry; message is a proposal, not a delivery",
                      verification_plan="Consent answer received before the visit; otherwise visit after checkout",
                      confidence=ep["conf"])

    # ------------------------------------------------------------------ decisions
    def handle(self, ctx, ts, kind, payload):
        if kind.startswith("gap"):
            return self._handle_gap(ctx, kind, payload)
        room = payload["room"]
        key = f"water:{room}"
        ep = self._ep.get(key) if ctx.log.is_open(key) else None

        if kind in ("loud_candidate", "loud", "subtle", "subtle_night", "subtle_confirm") and ep is None:
            why = self._suppressed(ctx, room)
            if why:
                self.ruled_out.append({"t": iso(ctx.t), "room": room, "kind": kind, "why": why})
                return

        if kind in ("loud_candidate", "loud"):
            occ = ctx.state.occupied(room, ctx.t)
            if kind == "loud_candidate" and occ:
                return  # occupied: could be a long bath/shower -> wait for the 2 h rule
            if ep is not None and ep["mode"] == "loud":
                return
            rate, start = self._recent(room, ctx.t, payload["start"])
            dur = (ctx.t + INTERVAL - start) / pd.Timedelta(hours=1)
            ev = [f"room_water.csv:{room}:{iso(start)}..{iso(ctx.t)}:~{rate:.0f} L/15min continuous "
                  f"{dur:.1f} h, {'occupied' if occ else 'vacant'}, not cleaning"] + self._bms_clue(ctx)
            self._ep[key] = ep = {"mode": "loud", "conf": 0.9 if not occ else 0.8, "inspected": False,
                                  "shutoff": False, "ev": ev}
            self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="flag_anomaly", priority="high",
                      reason=f"Loud leak suspected in {room}: ~{rate:.0f} L/15 min without pause for {dur:.1f} h "
                             f"({'occupied' if occ else 'vacant'}). Excluded: showers/baths are bursty (minutes), "
                             "no cleaning in progress, no meter reset, no legionella flush scheduled",
                      evidence=ev, confidence=ep["conf"],
                      expected_outcome="Operations aware within the first hours instead of next-day work order",
                      verification_plan=f"Flow on WM_{room} drops below {LOUD_L:.0f} L/15 min; if not within 1 h -> water shutoff request")
            self._inspect(ctx, room, key, f"Continuous high flow on WM_{room}", ev, "high")
            self._later(ctx.t + SHUTOFF_AFTER, "loud_shutoff", {"room": room})
        elif kind == "loud_shutoff":
            if ep is None or ep["mode"] != "loud" or ep["shutoff"]:
                return
            s = self.rate[room].loc[:ctx.t].dropna().tail(2)
            if s.empty or (s < LOUD_L).any():
                return
            occ = ctx.state.occupied(room, ctx.t)
            ep["shutoff"] = True
            self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="water_shutoff", priority="critical",
                      reason=f"Flow in {room} still ~{s.mean():.0f} L/15 min 1 h after the inspection request -> "
                             "propose isolating the room's water supply to limit water damage",
                      safety_relevant=True, enters_occupied_room=occ,
                      evidence=[f"room_water.csv:{room}:{iso(s.index[0])}..{iso(ctx.t)}:~{s.mean():.0f} L/15min"],
                      expected_outcome="Room supply isolated by staff after approval/consent; no automatic valve control",
                      verification_plan=f"WM_{room} reads ~0 after isolation; reopen after repair and verify no flow between uses",
                      confidence=ep["conf"])
        elif kind == "loud_end":
            if ep is not None and ep["mode"] == "loud":
                self._close(ctx, key, room, f"Flow on WM_{room} below {LOUD_L:.0f} L/15 min for 1 h")
        elif kind in ("subtle", "subtle_night"):
            if ep is not None:
                if kind == "subtle_night" and not ep["inspected"]:
                    self._subtle_inspect(ctx, room, key, payload)
                return
            if kind == "subtle":
                rate, start = self._recent(room, ctx.t, payload["start"])
                ev = [f"room_water.csv:{room}:{iso(start)}..{iso(ctx.t)}:~{rate:.1f} L/15min, no zero interval for "
                      f"{SUBTLE_N // 4} h"]
                reason = (f"Subtle leak suspected in {room}: small flow (~{rate:.1f} L/15 min) without a single zero "
                          f"interval for {SUBTLE_N // 4} h - typical running toilet. Excluded: normal use is bursty "
                          "with zero intervals between uses; post-exchange meter noise alternates with zeros")
            else:
                ev = [f"room_water.csv:{room}:night {payload['night']} 01:00-05:00 local:non-zero {payload['share']:.0%} "
                      f"of intervals (own history {payload['base']:.0%}), 2 nights in a row"]
                reason = (f"Subtle leak suspected in {room}: night flow non-zero in {payload['share']:.0%} of intervals "
                          f"on 2 consecutive nights vs {payload['base']:.0%} in the room's own history")
            occ = ctx.state.occupied(room, ctx.t)
            self._ep[key] = ep = {"mode": "subtle", "conf": 0.7, "inspected": False, "shutoff": False, "ev": ev}
            self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="flag_anomaly", priority="medium",
                      reason=reason + f" ({'occupied' if occ else 'vacant'})", evidence=ev, confidence=ep["conf"],
                      expected_outcome="Running toilet / dripping fitting found before the guest complains",
                      verification_plan=f"WM_{room} shows zero intervals again (>= 2 h); if flow persists 12 h -> inspection")
            if kind == "subtle_night":
                self._subtle_inspect(ctx, room, key, payload)
        elif kind == "subtle_confirm":
            if ep is not None and ep["mode"] == "subtle" and not ep["inspected"]:
                self._subtle_inspect(ctx, room, key, payload)
        elif kind == "subtle_end":
            if ep is not None and ep["mode"] == "subtle":
                self._close(ctx, key, room, f"WM_{room} back to zero between uses for 2 h")
        elif kind == "guest_water":
            if ep is None:
                return  # no meter evidence: handled as a guest request by the guest-service agent
            gid = payload["request_id"]
            ev = ep["ev"] + [f"guest_requests.csv:{gid}:{room}:{iso(ts)}:water_issue"]
            ep["conf"] = min(0.95, ep["conf"] + 0.2)
            if not ep["inspected"]:
                self._inspect(ctx, room, key, f"Guest reported a water issue ({gid}) matching the meter anomaly", ev,
                              "high", gr_ids=[gid])
            else:
                occ = ctx.state.occupied(room, ctx.t)
                self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="dispatch_technician",
                          priority="high", enters_occupied_room=occ, guest_request_ids=[gid], evidence=ev,
                          reason=f"Guest report {gid} confirms the water anomaly in {room} - escalate to a plumber visit",
                          expected_outcome="Fitting repaired; guest informed",
                          verification_plan=f"WM_{room} back to baseline after the visit",
                          confidence=ep["conf"])

    def _subtle_inspect(self, ctx, room, key, payload):
        start = payload.get("start")
        if start is not None:
            rate, start = self._recent(room, ctx.t, start)
            ev = [f"room_water.csv:{room}:{iso(start)}..{iso(ctx.t)}:~{rate:.1f} L/15min continuous"]
            why = f"Small continuous flow on WM_{room} persists for {SUBTLE_CONFIRM_N // 4} h"
        else:
            ev = [f"room_water.csv:{room}:night {payload['night']}:non-zero {payload['share']:.0%} of night intervals"]
            why = f"Night flow on WM_{room} persists over consecutive nights"
        self._ep[key]["ev"] = self._ep[key]["ev"] + ev
        self._inspect(ctx, room, key, why, self._ep[key]["ev"], "medium")

    def _close(self, ctx, key, room, why):
        self._add(ctx, key, affected_assets=[room, f"WM_{room}"], action="flag_anomaly", priority="low",
                  reason=f"Verification: {why} - leak episode closed (repair or stop of the flow observed on the meter)",
                  evidence=[f"room_water.csv:{room}:..{iso(ctx.t)}:flow back to baseline"],
                  verification_plan="New episode if continuous flow reappears", confidence=0.8)
        ctx.log.close(key)
        self._ep.pop(key, None)

    def _handle_gap(self, ctx, kind, p):
        key = "water:gap"
        assets = ["WM_MAIN", "BUILDING"]
        ev = [f"water_main.csv+room_water.csv:WM_MAIN:night {p['night']} 01:00-05:00 local:main - sum(rooms) median "
              f"{p['excess'] + p['base']:.1f} L/15min vs baseline {p['base']:.1f}"]
        if kind == "gap" and not ctx.log.is_open(key):
            self._add(ctx, key, affected_assets=assets, action="flag_anomaly", priority="high",
                      reason=f"Gap leak suspected: main meter minus all room meters is {p['excess']:.1f} L/15 min "
                             f"(~{p['excess'] * 4:.0f} L/h) above its rolling night baseline for {p['nights']} nights. "
                             "Room meters are normal -> loss in common areas, risers, DHW circulation or technical room",
                      evidence=ev + self._bms_clue(ctx), confidence=0.75,
                      expected_outcome="Leak outside the rooms located before visible damage",
                      verification_plan="Night gap returns to baseline after repair; if it persists a 3rd night -> inspection")
        elif kind == "gap_confirm" and ctx.log.is_open(key):
            self._add(ctx, key, affected_assets=assets, action="request_inspection", priority="high",
                      reason=f"Night gap still {p['excess']:.1f} L/15 min above baseline ({p['nights']} nights): inspect "
                             "technical room, DHW circulation, risers and common-area fittings (no guest room entry)",
                      evidence=ev, confidence=0.85,
                      expected_outcome="Leak located and repaired by technician/plumber",
                      verification_plan="Night median of main - sum(rooms) back within baseline the night after repair")
        elif kind == "gap_end" and ctx.log.is_open(key):
            self._add(ctx, key, affected_assets=assets, action="flag_anomaly", priority="low",
                      reason="Verification: night gap main - sum(rooms) back to baseline - gap leak episode closed",
                      evidence=ev, verification_plan="New episode if the night gap rises again", confidence=0.8)
            ctx.log.close(key)
