"""T04 Guest service agent: every guest request gets a timely handling decision in the guest's language.

Ownership (docs/tasks/T04-guest-service.md):
* early_checkin, late_checkout, waiting_for_room, arrival_delay -> turnaround agent (T05), skipped here.
* temperature_hot / temperature_cold / air_quality -> comfort agent (T02). We only send a ``notify_guest``
  acknowledgement if the comfort agent has made no decision for that room within 30 min of the request
  (checked causally in ``ctx.log`` at the check time, never with future information).
* water_issue -> corroborating evidence for the water agent (T01): if the water agent already has an episode
  for the room we only acknowledge and link to it, otherwise we request an inspection (approval, + consent
  when the room is occupied).
* Everything else is handled here: maintenance_issue, cleaning_request, extra_towels, noise_complaint,
  access_issue, wifi_help, parking, luggage_storage, invoice (and unknown future types -> generic ack).

Each first decision carries ``guest_request_ids=[request_id]``, a proposed message from
``plato40.messaging.compose_guest_message`` in the request language and is stamped at the first clock tick
after the request (<= response_due_utc). Staff are only scheduled on shifts known as 'scheduled' at decision
time. Consent/approval flags are derived by ``make_decision`` from policy hints only.
"""
from __future__ import annotations

import math
from collections import Counter

import pandas as pd

from ..clock import iso, local_hm
from ..decision import PRIORITIES, make_decision
from ..messaging import approval_note, compose_guest_message
from .base import TriggerDetector, floor_of

TURNAROUND_TYPES = {"early_checkin", "late_checkout", "waiting_for_room", "arrival_delay"}
COMFORT_TYPES = {"temperature_hot", "temperature_cold", "air_quality"}
INFO_TYPES = {"wifi_help", "parking", "luggage_storage", "invoice"}
COMFORT_WAIT = pd.Timedelta(minutes=30)      # give the comfort agent 30 min to own the request
TICK_MARGIN = pd.Timedelta(minutes=15)       # engine step; keeps the delayed ack <= response_due_utc
DEFAULT_DUE = pd.Timedelta(hours=2)          # if response_due_utc is missing
SLOT_HORIZON = pd.Timedelta(days=7)
ACCESS_LOOKBACK = pd.Timedelta(hours=1)
COMFORT_MSG = {"temperature_hot": "comfort_hot_update", "temperature_cold": "heating_update",
               "air_quality": "air_quality_update"}


def _ceil15(t: pd.Timestamp) -> pd.Timestamp:
    return t.ceil("15min")


def is_t04_owned(request_type) -> bool:
    """Requests T04 must answer itself (comfort acks are conditional, turnaround types belong to T05)."""
    return str(request_type) not in TURNAROUND_TYPES | COMFORT_TYPES


class GuestServiceAgent(TriggerDetector):
    name = "guest_service"

    def build_triggers(self, data, state):
        self.responses: list[dict] = []
        self.skipped: Counter = Counter()
        self._load: Counter = Counter()     # (resource, local day) -> tasks we assigned
        self._assigned: list[dict] = []     # staff tasks we scheduled (for re-planning on roster changes)
        self.replans = 0
        self._rooms = set(data.room_ids)
        for row in data.guest_requests.to_dict("records"):
            ts = row["timestamp_utc"]
            if pd.isna(ts):
                continue
            due = row.get("response_due_utc")
            if due is None or pd.isna(due):
                due = ts + DEFAULT_DUE
                row["response_due_utc"] = due
            rtype = str(row.get("request_type"))
            if rtype in TURNAROUND_TYPES:
                self.skipped[rtype] += 1
                continue
            if rtype in COMFORT_TYPES:
                wait = min(COMFORT_WAIT, max(pd.Timedelta(0), due - ts - TICK_MARGIN))
                yield ts + wait, "comfort_check", row
            else:
                yield ts, "request", row
        # roster changes become known at status_known_utc -> re-plan our tasks on that shift
        ros = data.roster
        changed = ros[(ros.status != "scheduled") & ros.status_known_utc.notna()]
        for sh in changed.itertuples(index=False):
            yield sh.status_known_utc, "roster_change", sh._asdict()

    # ------------------------------------------------------------------------ helpers
    def _room(self, row):
        r = row.get("room_id")
        return r if isinstance(r, str) and r in self._rooms else None

    @staticmethod
    def _prio(row) -> str:
        p = str(row.get("priority", "")).strip().lower()
        return p if p in PRIORITIES else "medium"

    @staticmethod
    def _consent_flag(row) -> bool:
        return str(row.get("requires_guest_consent", "")).strip().lower() == "required"

    def _occupied_entry(self, ctx, room, row) -> bool:
        """Entering this room counts as entering an occupied room (state says occupied or request says so)."""
        return bool(room) and (ctx.state.occupied(room, ctx.t) or self._consent_flag(row))

    def _evidence(self, row) -> list[str]:
        return [f"guest_requests.csv:{row['request_id']}:{iso(row['timestamp_utc'])}"]

    def _slot(self, ctx, role: str, earliest: pd.Timestamp, duration: pd.Timedelta):
        """Earliest (resource, slot) of `role` on a shift known as scheduled at ctx.t; least-loaded on ties."""
        earliest = _ceil15(earliest)
        r = ctx.asof.roster()
        r = r[(r.role == role) & (r.status == "scheduled") & (r.end_utc - duration >= earliest)
              & (r.start_utc < earliest + SLOT_HORIZON)]
        cands = []
        for sh in r.itertuples(index=False):
            slot = _ceil15(max(sh.start_utc, earliest))
            if slot + duration > sh.end_utc:
                continue
            cands.append((slot, self._load[(sh.resource_id, slot.date())], sh.resource_id))
        for slot, _, rid in sorted(cands):
            ok, _ = ctx.state.available(rid, slot, known_at=ctx.t)
            if ok:
                self._load[(rid, slot.date())] += 1
                return rid, slot
        return None, None

    def _add(self, ctx, row, decision, first: bool):
        rid = row["request_id"]
        ctx.log.add(decision, episode_key=f"guest:{rid}", prefix="GR")
        if first:
            self.responses.append({
                "request_id": rid, "request_type": str(row.get("request_type")),
                "latency_min": (ctx.t - row["timestamp_utc"]).total_seconds() / 60,
                "on_time": ctx.t <= row["response_due_utc"], "event_id": decision.event_id,
                "language_ok": (decision.message or {}).get("language") ==
                               (decision.message or {}).get("requested_language")})
        return decision

    def _notify(self, ctx, row, room, kind, reason, issue_type="guest_request", deps=None, first=True,
                expected="Guest informed in their language (proposal - delivery not confirmed)",
                verification="Guest reply / no follow-up complaint within 24 h", msg_reason=None, **facts):
        if msg_reason:
            facts["reason"] = msg_reason  # template fact 'reason' (decision reason is the `reason` argument)
        msg = compose_guest_message(kind, row.get("language"), **facts)
        d = make_decision(
            t=ctx.t, affected_assets=[room] if room else ["BUILDING"], issue_type=issue_type,
            action="notify_guest", priority=self._prio(row), reason=reason, evidence=self._evidence(row),
            guest_request_ids=[row["request_id"]], message=msg, dependencies=deps or [],
            expected_outcome=expected, verification_plan=verification, confidence=0.9, agent=self.name)
        return self._add(ctx, row, d, first)

    # ------------------------------------------------------------------------ dispatch
    def handle(self, ctx, ts, kind, row):
        if kind == "roster_change":
            self._replan(ctx, row)
            return
        rtype = str(row.get("request_type"))
        if kind == "comfort_check":
            self._comfort(ctx, row, rtype)
        elif rtype == "maintenance_issue":
            self._maintenance(ctx, row)
        elif rtype == "water_issue":
            self._water(ctx, row)
        elif rtype in ("cleaning_request", "extra_towels"):
            self._housekeeping(ctx, row, rtype)
        elif rtype in ("noise_complaint", "noise"):
            self._noise(ctx, row)
        elif rtype == "access_issue":
            self._access(ctx, row)
        else:
            room = self._room(row)
            known = rtype in INFO_TYPES
            self._notify(ctx, row, room, "service_ack", request_type=rtype if known else None, room_id=room,
                         reason=f"Guest request {row['request_id']} ({rtype}): "
                                + ("standard information answered autonomously" if known
                                   else "unknown request type - acknowledged, forwarded to staff"))
        ctx.log.close(f"guest:{row['request_id']}")

    # --- comfort (T02 owns; we only ack if it stays silent) --------------------------
    def _comfort(self, ctx, row, rtype):
        room = self._room(row)
        req_ts = row["timestamp_utc"]
        lo = iso(req_ts - COMFORT_WAIT)
        owners, context = [], []
        for d in reversed(ctx.log.decisions):
            if d.timestamp_utc < lo:
                break
            if row["request_id"] in d.guest_request_ids:
                owners.append(d.event_id)
            elif (d.agent == "comfort" or d.issue_type == "heating_comfort"
                  or (rtype == "air_quality" and d.issue_type == "ventilation_maintenance")):
                if room in d.affected_assets:
                    owners.append(d.event_id)
                elif "BUILDING" in d.affected_assets or "HP1" in d.affected_assets or "AHU1" in d.affected_assets:
                    context.append(d.event_id)
        if owners:
            self.skipped[f"{rtype}:owned_by_comfort"] += 1
            return
        deps = ["comfort agent: no room-level decision within 30 min of the request"] + context[:3]
        self._notify(ctx, row, room, COMFORT_MSG[rtype], room_id=room, deps=deps,
                     reason=f"Guest request {row['request_id']} ({rtype}) in {room or 'unknown room'}: comfort agent "
                            "made no decision for this room within 30 min -> acknowledge and keep the room on watch",
                     verification="Room temperature / air quality back in comfort band and no repeat request "
                                  "within 4 h; otherwise comfort agent escalates")

    # --- maintenance --------------------------------------------------------------------
    def _maintenance(self, ctx, row, issue_type="guest_request", reason_code="repair", what="technical issue"):
        room = self._room(row)
        rid = row["request_id"]
        enters = self._occupied_entry(ctx, room, row)
        tech, slot = self._slot(ctx, "technician", ctx.t + pd.Timedelta(hours=1), pd.Timedelta(hours=1))
        text = str(row.get("text", ""))[:120]
        deps = []
        if enters and room:
            if slot is not None:
                n = self._notify(
                    ctx, row, room, "maintenance_consent_request", issue_type=issue_type,
                    reason=f"Guest reported {what} in occupied {room} ({rid}): entry needs guest consent - "
                           f"asking for the proposed technician window",
                    expected="Guest consents to (or reschedules) the technician visit",
                    verification="Guest reply before the window; no entry without consent",
                    msg_reason=reason_code, room_id=room, proposed_window_local=f"{local_hm(slot)}-{local_hm(slot + pd.Timedelta(hours=1))}",
                    reply_by_local=slot - pd.Timedelta(minutes=15))
            else:
                n = self._notify(ctx, row, room, "service_ack", request_type=str(row.get("request_type")),
                                 room_id=room, issue_type=issue_type,
                                 reason=f"Guest reported {what} in occupied {room} ({rid}); no technician on shift "
                                        "within 7 days - acknowledged, time slot follows after approval")
            deps.append(f"guest consent for entering {room} (requested in {n.event_id})")
            first = False
        else:
            first = True
        why = (f"Guest request {rid}: {what} in {room or 'unknown room'} ('{text}'). "
               + ("Room occupied -> entry needs approval + guest consent. " if enters else "Room vacant. ")
               + (f"{tech} on shift at {local_hm(slot)} local." if slot is not None
                  else "No technician on shift within 7 days (as known now) -> approver to decide on contractor."))
        proposal = (f"Inspect {room} with {tech} at {iso(slot)}" if slot is not None
                    else f"Order contractor inspection of {room}")
        d = make_decision(
            t=ctx.t, affected_assets=[room] if room else ["BUILDING"], issue_type=issue_type,
            action="request_inspection", priority=self._prio(row), enters_occupied_room=enters,
            reason=approval_note(why, self._evidence(row), proposal,
                                 "defect persists, guest dissatisfied; possible follow-up damage"),
            evidence=self._evidence(row), guest_request_ids=[rid], dependencies=deps,
            resource_id=tech, scheduled_for_utc=slot,
            message=None if not first else compose_guest_message(
                "maintenance_scheduled" if slot is not None else "service_ack", row.get("language"),
                room_id=room, slot_local=slot, request_type=str(row.get("request_type"))),
            expected_outcome="Defect inspected and fixed; guest informed",
            verification_plan="Technician closes a work order for the room; no repeat request within 48 h",
            confidence=0.8, agent=self.name)
        self._add(ctx, row, d, first)
        if slot is not None:
            self._remember(row, d, room, "technician", pd.Timedelta(hours=1), issue_type)

    # --- water (T01 owns detection; guest report = corroborating evidence) -------------
    def _water(self, ctx, row):
        room = self._room(row)
        deps = []
        if room:
            lo = iso(ctx.t - pd.Timedelta(hours=48))
            for d in reversed(ctx.log.decisions):
                if d.timestamp_utc < lo:
                    break
                if (d.agent == "water" or d.issue_type == "water_leak") and room in d.affected_assets:
                    deps.append(d.event_id)
                    break
        if deps:
            self._notify(ctx, row, room, "service_ack", request_type="water_issue", room_id=room,
                         issue_type="water_leak", deps=deps,
                         reason=f"Guest report {row['request_id']} corroborates the open water episode for {room} "
                                f"({deps[0]}); acknowledged, inspection handled by the water agent")
            return
        self._maintenance(ctx, row, issue_type="water_leak", reason_code="water_leak", what="a water issue")

    # --- housekeeping ---------------------------------------------------------------------
    def _housekeeping(self, ctx, row, rtype):
        room = self._room(row)
        rid = row["request_id"]
        towels = rtype == "extra_towels"
        lead, dur = (pd.Timedelta(minutes=15), pd.Timedelta(minutes=15)) if towels else \
            (pd.Timedelta(minutes=30), pd.Timedelta(minutes=45))
        cleaner, slot = self._slot(ctx, "cleaner", ctx.t + lead, dur)
        if cleaner is None or room is None:
            self._notify(ctx, row, room, "service_ack", request_type=rtype, room_id=room,
                         reason=f"Guest request {rid} ({rtype}): no cleaner on shift within 7 days (as known now) "
                                "- acknowledged, staff to plan manually")
            return
        # towels are handed over at the door (no entry); cleaning enters the room -> consent if occupied
        enters = False if towels else self._occupied_entry(ctx, room, row)
        next_shift = slot > ctx.t + lead + pd.Timedelta(minutes=15)
        msg = compose_guest_message("towels_delivery" if towels else "cleaning_scheduled", row.get("language"),
                                    room_id=room, slot_local=slot)
        d = make_decision(
            t=ctx.t, affected_assets=[room, cleaner], issue_type="guest_request", action="schedule_cleaning",
            priority=self._prio(row), enters_occupied_room=enters,
            reason=(f"Guest request {rid} ({rtype}) for {room}: {cleaner} on shift at {local_hm(slot)} local"
                    + (" (no housekeeping on shift now - next shift)" if next_shift else "")
                    + ("; handed over at the door, no room entry" if towels else
                       ("; entering occupied room only with guest consent" if enters else ""))),
            evidence=self._evidence(row), guest_request_ids=[rid], resource_id=cleaner, scheduled_for_utc=slot,
            message=msg, expected_outcome=("Towels delivered" if towels else "Room cleaned") + " at the proposed time",
            verification_plan="Housekeeping marks the task done; no repeat request within 24 h",
            confidence=0.85, agent=self.name)
        self._add(ctx, row, d, True)
        self._remember(row, d, room, "cleaner", dur, "guest_request", kind="towels_delivery" if towels
                       else "cleaning_scheduled", enters=enters)

    # --- noise ------------------------------------------------------------------------------
    def _noise(self, ctx, row):
        room = self._room(row)
        rid = row["request_id"]
        self._notify(ctx, row, room, "noise_ack", room_id=room,
                     reason=f"Noise complaint {rid} from {room or 'unknown room'}: acknowledged")
        gr = ctx.asof.window(ctx.data.guest_requests, pd.Timedelta(days=7))
        gr = gr[gr.request_type.astype(str).isin(["noise_complaint", "noise"])]
        same_floor = gr[gr.room_id.astype(str).str.match(rf"R{floor_of(room)}\d\d$")] if room else gr.iloc[:0]
        d = make_decision(
            t=ctx.t, affected_assets=[room] if room else ["BUILDING"], issue_type="guest_request",
            action="flag_anomaly", priority=self._prio(row),
            reason=(f"Noise complaint {rid} from {room or 'unknown room'} at {local_hm(row['timestamp_utc'])} local; "
                    f"{len(same_floor)} noise complaint(s) on this floor in the last 7 days -> staff to check corridor "
                    "/ neighbouring rooms (no room entry)"),
            evidence=self._evidence(row), guest_request_ids=[rid],
            expected_outcome="Source of noise addressed",
            verification_plan="No further noise complaint from this floor within 2 h",
            confidence=0.7, agent=self.name)
        self._add(ctx, row, d, False)

    # --- access -----------------------------------------------------------------------------
    def _access(self, ctx, row):
        room = self._room(row)
        rid = row["request_id"]
        booking = row.get("booking_id")
        self._notify(ctx, row, room, "access_issue_ack", room_id=room,
                     reason=f"Access issue {rid} ({room or 'unknown room'}): acknowledged immediately")
        acc = ctx.asof.window(ctx.data.access, ACCESS_LOOKBACK)
        mine = acc[(acc.door_id == room) | (acc.credential == booking)] if room or isinstance(booking, str) else acc.iloc[:0]
        denied = mine[mine.result == "denied"]
        if len(denied):
            doors = sorted(set(denied.door_id))
            reason = (f"Access issue {rid}: {len(denied)} denied attempt(s) in the last hour on {', '.join(doors)} "
                      f"(credential {booking}) -> credential/lock problem; re-issue digital key via ACS1, "
                      "staff on call if it persists")
            ev = [f"access_log.csv:{r.door_id}:{iso(r.timestamp_utc)}:denied" for r in denied.itertuples(index=False)][:5]
            conf = 0.85
        else:
            reason = (f"Access issue {rid}: no denied attempts logged for {room or 'the room'} / {booking} in the last "
                      "hour -> credential possibly not presented or lock offline; check ACS1 and contact guest")
            ev = [f"access_log.csv:{room or 'ACS1'}:{iso(ctx.t - ACCESS_LOOKBACK)}..{iso(ctx.t)}:no denied"]
            conf = 0.6
        d = make_decision(
            t=ctx.t, affected_assets=[a for a in (room, "ACS1") if a], issue_type="guest_request",
            action="flag_anomaly", priority=self._prio(row), reason=reason, evidence=self._evidence(row) + ev,
            guest_request_ids=[rid], expected_outcome="Guest can enter the room again",
            verification_plan="Next access_log entry for this door/credential is 'granted' within 30 min; "
                              "otherwise escalate to staff",
            confidence=conf, agent=self.name)
        self._add(ctx, row, d, False)

    # --- re-planning ----------------------------------------------------------------------
    def _remember(self, row, d, room, role, dur, issue_type, kind=None, enters=None):
        self._assigned.append({"row": row, "event_id": d.event_id, "episode_id": d.episode_id, "room": room,
                               "role": role, "dur": dur, "issue_type": issue_type, "kind": kind, "enters": enters,
                               "resource": d.resource_id, "slot": pd.Timestamp(d.scheduled_for_utc)})

    def _replan(self, ctx, sh):
        """A shift we planned on is now known as sick/vacation/out of service -> move the task (follow-up)."""
        for a in self._assigned:
            if not (a["resource"] == sh["resource_id"] and sh["start_utc"] <= a["slot"] < sh["end_utc"]
                    and a["slot"] > ctx.t):
                continue
            row, room = a["row"], a["room"]
            rid, slot = self._slot(ctx, a["role"], max(ctx.t + pd.Timedelta(minutes=15), a["slot"]), a["dur"])
            why = (f"Re-plan for request {row['request_id']}: {sh['resource_id']} shift {sh['shift_id']} now "
                   f"{sh['status']} (known {iso(sh['status_known_utc'])}); task {a['event_id']} at "
                   f"{local_hm(a['slot'])} local cannot happen")
            common = dict(t=ctx.t, priority=self._prio(row), evidence=self._evidence(row) + [
                f"staff_roster.csv:{sh['shift_id']}:{sh['resource_id']}:{sh['status']} known "
                f"{iso(sh['status_known_utc'])}"], guest_request_ids=[row["request_id"]],
                dependencies=[a["event_id"]], episode_id=a["episode_id"], agent=self.name)
            old_res = a["resource"]
            if rid is None:
                d = make_decision(affected_assets=[room], issue_type=a["issue_type"], action="request_human_approval",
                                  reason=why + "; no replacement on shift within 7 days -> staff to decide",
                                  verification_plan="Staff confirm a new slot", confidence=0.7, **common)
                a["resource"] = None
            elif a["role"] == "cleaner":
                d = make_decision(
                    affected_assets=[room, rid], issue_type=a["issue_type"], action="schedule_cleaning",
                    enters_occupied_room=bool(a["enters"]), reason=why + f" -> moved to {rid} at {local_hm(slot)} local",
                    resource_id=rid, scheduled_for_utc=slot,
                    message=compose_guest_message(a["kind"], row.get("language"), room_id=room, slot_local=slot),
                    verification_plan="Housekeeping marks the task done; no repeat request within 24 h",
                    expected_outcome="Task done at the new time; guest informed of the new time",
                    confidence=0.8, **common)
            else:
                d = make_decision(
                    affected_assets=[room], issue_type=a["issue_type"], action="request_inspection",
                    enters_occupied_room=self._occupied_entry(ctx, room, row),
                    reason=why + f" -> moved to {rid} at {iso(slot)}", resource_id=rid, scheduled_for_utc=slot,
                    message=compose_guest_message("maintenance_scheduled", row.get("language"), room_id=room,
                                                  slot_local=slot),
                    verification_plan="Technician closes a work order for the room; no repeat request within 48 h",
                    expected_outcome="Inspection at the new time; guest informed", confidence=0.75, **common)
            ctx.log.add(d)
            self.replans += 1
            self._load[(old_res, a["slot"].date())] -= 1
            a.update(resource=rid, slot=slot if rid else a["slot"], event_id=d.event_id)

    # ------------------------------------------------------------------------ report
    def finish(self, ctx):
        stats = response_stats(self.responses)
        if stats["n"]:
            print(f"[guest_service] {stats['n']} requests answered, on time {stats['on_time']}/{stats['n']}, "
                  f"response median {stats['median_min']:.1f} min, p95 {stats['p95_min']:.1f} min; "
                  f"re-plans {self.replans}, skipped {dict(self.skipped)}")


def response_stats(responses: list[dict]) -> dict:
    lat = sorted(r["latency_min"] for r in responses)
    if not lat:
        return {"n": 0, "on_time": 0, "median_min": math.nan, "p95_min": math.nan}
    s = pd.Series(lat)
    return {"n": len(lat), "on_time": sum(r["on_time"] for r in responses),
            "median_min": float(s.median()), "p95_min": float(s.quantile(0.95))}
