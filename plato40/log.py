"""Decision log with episodes.

An episode = one incident. Repeated alerts for one incident count as ONE detection
and alert spam counts negatively (CHALLENGE §9), so detectors open an episode once,
add follow-up decisions only when the situation changes, and close it.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .decision import Decision


class DecisionLog:
    def __init__(self):
        self.decisions: list[Decision] = []
        self._open: dict[str, str] = {}      # episode key -> episode_id
        self._ep_counter = 0

    # ---- episodes -----------------------------------------------------------
    def is_open(self, key: str) -> bool:
        return key in self._open

    def episode(self, key: str, prefix: str = "EP") -> str:
        """Return the open episode id for key, opening a new one if needed."""
        if key not in self._open:
            self._ep_counter += 1
            self._open[key] = f"{prefix}-{self._ep_counter:05d}"
        return self._open[key]

    def close(self, key: str) -> None:
        self._open.pop(key, None)

    def open_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._open if k.startswith(prefix)]

    # ---- decisions ------------------------------------------------------------
    def add(self, d: Decision, episode_key: str | None = None, prefix: str = "EP") -> Decision:
        d.event_id = f"D{len(self.decisions) + 1:06d}"
        if episode_key is not None and d.episode_id is None:
            d.episode_id = self.episode(episode_key, prefix)
        self.decisions.append(d)
        return d

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ds = sorted(self.decisions, key=lambda d: (d.timestamp_utc, d.event_id))
        with path.open("w", encoding="utf-8") as f:
            for d in ds:
                f.write(json.dumps(d.to_json(), ensure_ascii=False) + "\n")
        return path

    def summary(self) -> dict:
        return {
            "decisions": len(self.decisions),
            "episodes": len({d.episode_id for d in self.decisions if d.episode_id}),
            "by_issue": dict(Counter(d.issue_type for d in self.decisions)),
            "by_action": dict(Counter(d.action for d in self.decisions)),
            "needs_approval": sum(d.requires_human_approval for d in self.decisions),
        }
