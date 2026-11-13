"""Guest messaging + approval notes (interface stub - implemented in ticket T09).

Other agents call these functions now; T09 replaces the bodies with multilingual
templates (de, en, it, fr, es, nl), requested-time parsing and optional LLM wording.
The signatures are the contract - keep them stable.

A message produced here is a PROPOSAL. It is never proof of delivery.
"""
from __future__ import annotations

import re

SUPPORTED_LANGUAGES = ("de", "en", "it", "fr", "es", "nl")

# kinds used by agents (extend in T09, keep these names)
KINDS = (
    "room_ready_eta",          # facts: room_id, eta_local (HH:MM)
    "early_checkin_possible",  # facts: room_id, eta_local
    "early_checkin_not_possible",  # facts: earliest_local
    "late_checkout_ok",        # facts: until_local
    "late_checkout_not_possible",  # facts: until_local (standard 11:00)
    "waiting_update",          # facts: room_id, eta_local
    "maintenance_consent_request",  # facts: room_id, reason, proposed_window_local
    "service_ack",             # facts: request_type
    "heating_update",          # facts: room_id
)


def compose_guest_message(kind: str, language: str | None, **facts) -> dict:
    """Return {'language', 'text', 'source'} - source is 'template' or 'llm'.

    Stub: English template only. T09 implements all languages.
    """
    lang = language if language in SUPPORTED_LANGUAGES else "en"
    eta = facts.get("eta_local", "")
    room = facts.get("room_id", "your room")
    texts = {
        "room_ready_eta": f"Your room {room} will be ready at about {eta}.",
        "early_checkin_possible": f"Early check-in is possible: room {room} ready at about {eta}.",
        "early_checkin_not_possible": f"Sorry, early check-in is not possible. Your room will be ready from {facts.get('earliest_local', '15:00')}.",
        "late_checkout_ok": f"Late check-out until {facts.get('until_local', '')} is confirmed.",
        "late_checkout_not_possible": f"Sorry, late check-out is not possible today; check-out is until {facts.get('until_local', '11:00')}.",
        "waiting_update": f"Thank you for waiting - room {room} is being prepared, expected ready at {eta}.",
        "maintenance_consent_request": f"May our technician enter room {room} ({facts.get('reason', '')}) during {facts.get('proposed_window_local', '')}? Please confirm.",
        "service_ack": "Thank you, we have received your request and will take care of it.",
        "heating_update": f"We are aware of the temperature issue in room {room} and are working on it.",
    }
    return {"language": lang if lang == "en" else "en", "text": texts.get(kind, texts["service_ack"]),
            "source": "template", "requested_language": lang}


_TIME = re.compile(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\b")


def parse_requested_time(text: str) -> str | None:
    """Extract a requested clock time 'HH:MM' from free text (stub: first hh:mm). T09 hardens it."""
    m = _TIME.search(text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def approval_note(decision_reason: str, evidence: list[str], proposal: str, if_no_action: str) -> str:
    """Short note for the human approver (stub). T09 may add LLM wording."""
    ev = "; ".join(evidence[:3])
    return f"{decision_reason} | Proposal: {proposal} | Evidence: {ev} | If no action: {if_no_action}"
