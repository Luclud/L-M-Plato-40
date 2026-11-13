"""Autonomy policy (CHALLENGE §7). These rules must never be weakened."""
import pytest

from plato40.decision import (APPROVAL_AND_CONSENT, AUTONOMOUS, HUMAN_APPROVAL, PolicyError,
                              autonomy_level, make_decision)


@pytest.mark.parametrize("action,level", [
    ("schedule_cleaning", AUTONOMOUS), ("dispatch_robot", AUTONOMOUS), ("notify_guest", AUTONOMOUS),
    ("flag_anomaly", AUTONOMOUS), ("optimize_energy", AUTONOMOUS),
    ("dispatch_technician", HUMAN_APPROVAL), ("block_room", HUMAN_APPROVAL), ("reassign_room", HUMAN_APPROVAL),
    ("request_inspection", HUMAN_APPROVAL), ("order_service", HUMAN_APPROVAL),
    ("water_shutoff", APPROVAL_AND_CONSENT),
])
def test_base_levels(action, level):
    assert autonomy_level(action) == level


def test_routine_services_autonomous_unless_costly():
    assert autonomy_level("order_service", service="waste_pickup") == AUTONOMOUS
    assert autonomy_level("order_service", service="vending_restock") == AUTONOMOUS
    assert autonomy_level("order_service", service="waste_pickup", notable_cost=True) == HUMAN_APPROVAL
    assert autonomy_level("order_service", service="hvac_contractor") == HUMAN_APPROVAL


def test_occupied_entry_needs_consent():
    assert autonomy_level("request_inspection", enters_occupied_room=True) == APPROVAL_AND_CONSENT
    assert autonomy_level("schedule_cleaning", enters_occupied_room=True) == APPROVAL_AND_CONSENT


def test_setpoint_outside_band_needs_approval():
    assert autonomy_level("adjust_room_setpoint") == AUTONOMOUS
    assert autonomy_level("adjust_room_setpoint", outside_comfort_band=True) == HUMAN_APPROVAL


def test_make_decision_flags():
    d = make_decision(t="2026-01-10T10:00:00Z", affected_assets=["R104"], issue_type="water_leak",
                      action="water_shutoff", priority="critical", reason="x")
    assert d.requires_human_approval and d.requires_guest_consent and d.autonomy == APPROVAL_AND_CONSENT


def test_unknown_action_and_bad_priority_rejected():
    with pytest.raises(PolicyError):
        autonomy_level("open_all_doors")
    with pytest.raises(PolicyError):
        make_decision(t="2026-01-10T10:00:00Z", affected_assets=["R1"], issue_type="x", action="flag_anomaly",
                      priority="urgent", reason="x")
