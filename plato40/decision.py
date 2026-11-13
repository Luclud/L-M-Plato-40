"""Decision record + autonomy policy (CHALLENGE §7 "Safe by design").

Detectors never set requires_human_approval / requires_guest_consent themselves:
`make_decision` derives them from the action and the context hints, and can only
make a decision *stricter*, never looser.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .clock import iso

PRIORITIES = ("critical", "high", "medium", "low")

# Canonical issue types - use exactly these (eval/evaluate.py matches on them).
ISSUE_TYPES = {
    "room_turnaround",          # S01
    "water_leak",               # S02
    "heating_comfort",          # S03 (incl. heat pump degradation)
    "ventilation_maintenance",  # S04
    "occupancy_surge",          # S05
    "energy_optimization",      # battery / load shifting
    "logistics",                # waste, vending, robot bin runs
    "guest_request",            # guest service not covered above
    "data_quality",             # sensor / meter / gateway problems
}

ACTIONS = {
    "schedule_cleaning", "dispatch_robot", "adjust_room_setpoint", "optimize_energy",
    "block_room", "release_room", "reassign_room", "request_inspection",
    "dispatch_technician", "order_service", "notify_guest", "flag_anomaly",
    "defer_action", "request_human_approval",
}
# Extra actions (documented in README "Action vocabulary"):
EXTRA_ACTIONS = {
    "water_shutoff",          # isolate water supply of a room/riser - safety relevant
    "call_oncall_housekeeper",  # ONCALL_1, costly, waiting guests only
}
ALL_ACTIONS = ACTIONS | EXTRA_ACTIONS

AUTONOMOUS = "autonomous"
HUMAN_APPROVAL = "human_approval"
APPROVAL_AND_CONSENT = "approval_and_consent"
_LEVEL_RANK = {AUTONOMOUS: 0, HUMAN_APPROVAL: 1, APPROVAL_AND_CONSENT: 2}

# Base level per action. order_service is autonomous only for routine logistics.
_BASE = {
    "schedule_cleaning": AUTONOMOUS,
    "dispatch_robot": AUTONOMOUS,
    "adjust_room_setpoint": AUTONOMOUS,   # escalated if outside comfort band in occupied room
    "optimize_energy": AUTONOMOUS,
    "release_room": AUTONOMOUS,           # only after preconditions (State.can_release)
    "notify_guest": AUTONOMOUS,
    "flag_anomaly": AUTONOMOUS,
    "defer_action": AUTONOMOUS,
    "order_service": HUMAN_APPROVAL,      # contractors; see ROUTINE_SERVICES
    "block_room": HUMAN_APPROVAL,
    "reassign_room": HUMAN_APPROVAL,
    "request_inspection": HUMAN_APPROVAL,
    "dispatch_technician": HUMAN_APPROVAL,
    "request_human_approval": HUMAN_APPROVAL,
    "call_oncall_housekeeper": HUMAN_APPROVAL,  # notable cost
    "water_shutoff": APPROVAL_AND_CONSENT,
}
ROUTINE_SERVICES = {"waste_pickup", "vending_restock"}  # CHALLENGE §7: autonomous


class PolicyError(ValueError):
    """Raised when a detector tries to emit an unsafe decision."""


def autonomy_level(action: str, *, enters_occupied_room: bool = False, notable_cost: bool = False,
                   safety_relevant: bool = False, service: str | None = None,
                   outside_comfort_band: bool = False) -> str:
    if action not in ALL_ACTIONS:
        raise PolicyError(f"unknown action {action!r} - add it to EXTRA_ACTIONS and document it")
    level = _BASE[action]
    if action == "order_service" and service in ROUTINE_SERVICES and not notable_cost:
        level = AUTONOMOUS
    if action == "adjust_room_setpoint" and outside_comfort_band:
        level = max(level, HUMAN_APPROVAL, key=_LEVEL_RANK.get)
    if notable_cost:
        level = max(level, HUMAN_APPROVAL, key=_LEVEL_RANK.get)
    if enters_occupied_room or safety_relevant:
        level = APPROVAL_AND_CONSENT
    return level


@dataclass
class Decision:
    event_id: str
    timestamp_utc: str
    affected_assets: list[str]
    issue_type: str
    action: str
    priority: str
    reason: str
    requires_human_approval: bool
    autonomy: str
    requires_guest_consent: bool = False
    episode_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    expected_outcome: str | None = None
    verification_plan: str | None = None
    dependencies: list[str] = field(default_factory=list)
    guest_request_ids: list[str] = field(default_factory=list)
    resource_id: str | None = None
    confidence: float | None = None
    # extensions (documented in README)
    scheduled_for_utc: str | None = None
    message: dict | None = None     # proposed guest message {language, text, source}; NOT a delivery
    simulated_effect: str | None = None
    agent: str | None = None        # which domain agent produced it

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, [], {})}


def make_decision(*, t, affected_assets: list[str], issue_type: str, action: str, priority: str,
                  reason: str, enters_occupied_room: bool = False, notable_cost: bool = False,
                  safety_relevant: bool = False, service: str | None = None,
                  outside_comfort_band: bool = False, **optional) -> Decision:
    """Build a Decision with autonomy flags derived from policy. event_id is assigned by DecisionLog."""
    if priority not in PRIORITIES:
        raise PolicyError(f"priority must be one of {PRIORITIES}")
    if issue_type not in ISSUE_TYPES:
        raise PolicyError(f"issue_type {issue_type!r} not canonical - use one of {sorted(ISSUE_TYPES)}")
    if not affected_assets:
        raise PolicyError("affected_assets must be non-empty")
    level = autonomy_level(action, enters_occupied_room=enters_occupied_room, notable_cost=notable_cost,
                           safety_relevant=safety_relevant, service=service,
                           outside_comfort_band=outside_comfort_band)
    for k in ("scheduled_for_utc",):
        if optional.get(k) is not None and not isinstance(optional[k], str):
            optional[k] = iso(optional[k])
    return Decision(
        event_id="",
        timestamp_utc=iso(t),
        affected_assets=list(affected_assets),
        issue_type=issue_type,
        action=action,
        priority=priority,
        reason=reason,
        requires_human_approval=level != AUTONOMOUS,
        requires_guest_consent=level == APPROVAL_AND_CONSENT,
        autonomy=level,
        **optional,
    )
