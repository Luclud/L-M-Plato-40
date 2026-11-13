"""Illustrative quickstart decisions for the viewer demo until the scenario agents (T01-T06) land.

    python viewer/sample_decisions.py [data/quickstart] [viewer/sample/quickstart_illustrative.jsonl]

NOT pipeline output: hand-placed on the quickstart incident map (docs/DATA.md) so the viewer's
hero screens can be demonstrated. Every reason starts with "ILLUSTRATIVE", agent = "viewer_sample".
Flags still come from the autonomy policy (`make_decision`) and the file passes validate.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plato40 import io  # noqa: E402
from plato40.decision import make_decision  # noqa: E402
from plato40.log import DecisionLog  # noqa: E402
from plato40.state import State  # noqa: E402

TAG = "ILLUSTRATIVE (viewer sample, not pipeline output): "
OUT = ROOT / "viewer" / "sample" / "quickstart_illustrative.jsonl"


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def build(data_dir: str | Path = ROOT / "data" / "quickstart", out: str | Path = OUT) -> Path:
    data = io.load(data_dir)
    st = State(data)
    log = DecisionLog()

    def add(key, t, **kw):
        t = T(t)
        rid = kw.get("resource_id")
        if rid:
            ok, why = st.available(rid, T(kw.get("scheduled_for_utc") or t), known_at=t)
            assert ok, f"{rid} not available: {why}"
        kw["reason"] = TAG + kw["reason"]
        log.add(make_decision(t=t, agent="viewer_sample", **kw), episode_key=key)

    # S02 loud - R104 burst hose
    add("water:R104", "2026-01-12T18:00:00Z", affected_assets=["R104", "WM_R104"], issue_type="water_leak",
        action="flag_anomaly", priority="high", confidence=0.85,
        reason="R104 room meter ~27 L/15 min for 1 h, 4 intervals in a row, far above the room's night baseline",
        evidence=["room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T18:00:00Z:~27 L/15min vs baseline ~0"],
        expected_outcome="Flow source identified before damage spreads",
        verification_plan="Rate drops to baseline after inspection; escalate if > 20 L/15 min persists 1 h")
    add("water:R104", "2026-01-12T18:15:00Z", affected_assets=["R104"], issue_type="water_leak",
        action="request_inspection", priority="critical", confidence=0.85,
        enters_occupied_room=st.occupied("R104", T("2026-01-12T18:15:00Z")),
        reason="Continuous high flow, no shower pattern (no pauses) -> likely burst hose; on-site check needed",
        evidence=["room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T18:15:00Z:~27 L/15min continuous"],
        expected_outcome="Technician confirms leak", verification_plan="Inspection report; meter rate returns to 0")
    add("water:R104", "2026-01-12T18:30:00Z", affected_assets=["R104", "WM_R104"], issue_type="water_leak",
        action="water_shutoff", priority="critical", safety_relevant=True, confidence=0.8,
        reason="~100 L/h for 90 min; isolate the room supply once approved and (if occupied) consented",
        evidence=["room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T18:30:00Z:~150 L in 90 min"],
        simulated_effect="SIMULATED: ~2,700 L of the ~3,000 L incident volume avoided if isolated now (estimate)",
        verification_plan="WM_R104 rate = 0 within 15 min of shutoff; otherwise escalate to riser valve")
    add("water:R104", "2026-01-12T18:30:00Z", affected_assets=["R104"], issue_type="water_leak",
        action="notify_guest", priority="high",
        reason="Inform guest about inspection and possible short water interruption",
        message={"language": "en", "source": "template",
                 "text": "We detected unusual water flow in your room. A technician will check it shortly; "
                         "the water may be switched off briefly. Sorry for the inconvenience."},
        verification_plan="Guest reply or door access log; message delivery is not confirmed")

    # S03 - HP1 cold snap (context for the incident list)
    add("comfort:HP1", "2026-01-15T07:00:00Z", affected_assets=["HP1"], issue_type="heating_comfort",
        action="flag_anomaly", priority="high", confidence=0.7,
        reason="HP1 COP below what the outdoor temperature explains while the forecast stays below -9 °C",
        evidence=["equipment.csv:HP1:2026-01-14T00:00:00Z..2026-01-15T07:00:00Z:COP 2.8 -> 2.1",
                  "weather_forecast.csv:*:2026-01-15T05:00:00Z:issued, min -13 °C"],
        verification_plan="COP back within expected band; occupied rooms ≥ 20 °C")

    # S04 - AHU1 filter cascade
    add("maint:AHU1", "2026-01-15T12:00:00Z", affected_assets=["AHU1"], issue_type="ventilation_maintenance",
        action="flag_anomaly", priority="medium", confidence=0.7,
        reason="filter_dp_pa daily mean rising 92 -> 135 Pa within 3 days; airflow still ok",
        evidence=["equipment.csv:AHU1:2026-01-12T00:00:00Z..2026-01-15T12:00:00Z:filter_dp_pa 92 -> 135 Pa"],
        verification_plan="Trend continues -> order filter service; flat -> close")
    add("maint:AHU1", "2026-01-17T04:00:00Z", affected_assets=["AHU1"], issue_type="ventilation_maintenance",
        action="defer_action", priority="medium", confidence=0.6,
        reason="filter_dp_pa missing since 16 Jan 22:30Z - a data gap is unknown, not a repair; episode stays open",
        evidence=["equipment.csv:AHU1:2026-01-16T22:30:00Z..2026-01-17T04:00:00Z:filter_dp_pa missing"],
        verification_plan="When readings return, compare with pre-gap trend")
    add("maint:AHU1", "2026-01-17T08:00:00Z", affected_assets=["AHU1"], issue_type="ventilation_maintenance",
        action="order_service", priority="high", notable_cost=True, service="contractor", confidence=0.7,
        reason="Last valid Δp 165 Pa and rising; TECH_1 on vacation 19-23 Jan (known since Dec) -> contractor "
               "with lift platform",
        evidence=["equipment.csv:AHU1:2026-01-14T00:00:00Z..2026-01-16T22:15:00Z:filter_dp_pa 110 -> 165 Pa",
                  "staff_roster.csv:TECH_1:vacation 19-23 Jan (status known since Dec)"],
        expected_outcome="Filters replaced before airflow drops", verification_plan="filter_dp_pa < 100 Pa after service")

    # S05 - event weekend
    add("surge:BUILDING", "2026-01-22T06:00:00Z", affected_assets=["BUILDING"], issue_type="occupancy_surge",
        action="request_human_approval", priority="high", confidence=0.7,
        reason="25 confirmed arrivals on 23 Jan vs typical ~10; housekeeping capacity tight -> ask for extra shift",
        evidence=["bookings.csv:BUILDING:2026-01-22T06:00:00Z:25 arrivals on 2026-01-23 (as known now)"],
        verification_plan="All arriving rooms clean by 15:00 local; waiting_for_room events = 0")
    add("surge:BUILDING", "2026-01-23T05:00:00Z", affected_assets=["BUILDING", "ROBOT_1"],
        issue_type="occupancy_surge", action="dispatch_robot", priority="medium", resource_id="ROBOT_1",
        scheduled_for_utc="2026-01-23T07:00:00Z",
        reason="Robot cleans corridors/floors so cleaners focus on rooms",
        simulated_effect="SIMULATED: ~45 min cleaner time freed (estimate, offline replay)",
        verification_plan="robot_floor_cleaning_completed event; corridors done before 10:00 local")
    add("surge:BUILDING", "2026-01-24T05:45:00Z", affected_assets=["R403"], issue_type="occupancy_surge",
        action="schedule_cleaning", priority="high", resource_id="CLEANER_2",
        scheduled_for_utc="2026-01-24T08:00:00Z",
        reason="Re-plan: CLEANER_1 sick (known 05:30Z); R403 has an early arrival -> first in CLEANER_2's queue",
        evidence=["staff_roster.csv:CLEANER_1:2026-01-24T05:30:00Z:sick"],
        verification_plan="housekeeping_state=clean before release; never release before")
    add("surge:BUILDING", "2026-01-24T05:45:00Z", affected_assets=["R403"], issue_type="occupancy_surge",
        action="notify_guest", priority="medium",
        reason="Give the arriving guest a realistic ready time",
        message={"language": "de", "source": "template",
                 "text": "Ihr Zimmer ist voraussichtlich ab 15:30 Uhr bereit. Wir melden uns, sobald es fertig ist."},
        verification_plan="No waiting_for_room event; message delivery not confirmed")
    add("surge:BUILDING", "2026-01-24T05:50:00Z", affected_assets=["R403", "ONCALL_1"],
        issue_type="occupancy_surge", action="call_oncall_housekeeper", priority="high", resource_id="ONCALL_1",
        scheduled_for_utc="2026-01-24T14:30:00Z",
        reason="If R403 is still dirty when the guest waits, ONCALL_1 (15:30-22:00 local, costly) cleans it",
        verification_plan="Room clean within ~90 min of call; one room at a time")

    # S02 subtle - R305 toilet
    add("water:R305", "2026-01-20T05:00:00Z", affected_assets=["R305", "WM_R305"], issue_type="water_leak",
        action="flag_anomaly", priority="medium", confidence=0.6,
        reason="~1 L/15 min almost every interval, incl. 00-04Z when the room is normally silent",
        evidence=["room_water.csv:R305:2026-01-19T00:00:00Z..2026-01-20T04:00:00Z:night use ~18 L vs 0-6"],
        verification_plan="Night use back to 0-6 L; escalate if it persists another night")
    add("water:R305", "2026-01-21T05:00:00Z", affected_assets=["R305"], issue_type="water_leak",
        action="request_inspection", priority="medium", confidence=0.7,
        enters_occupied_room=st.occupied("R305", T("2026-01-21T05:00:00Z")),
        reason="Second night with constant trickle -> likely toilet fill valve; check at next vacant slot",
        evidence=["room_water.csv:R305:2026-01-20T00:00:00Z..2026-01-21T04:00:00Z:night use ~18 L"],
        verification_plan="Plumber report; night use back to baseline")

    return log.write(out)


if __name__ == "__main__":
    a = sys.argv[1:]
    print(build(*(a[:1] or [ROOT / "data" / "quickstart"]), *(a[1:2] or [OUT])))
