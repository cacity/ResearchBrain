from __future__ import annotations

from dataclasses import dataclass


class InvalidResearchTransition(ValueError):
    pass


TRANSITIONS: dict[str, set[str]] = {
    "queued": {"intake", "cancelled", "failed"},
    "intake": {"planning", "cancelled", "failed"},
    "planning": {"local_search", "online_search", "cancelled", "failed"},
    "local_search": {"evidence_inspection", "cancelled", "failed"},
    "evidence_inspection": {"gap_assessment", "cancelled", "failed"},
    "gap_assessment": {
        "local_search",
        "online_search",
        "acquisition_wait",
        "synthesis",
        "cancelled",
        "failed",
    },
    "online_search": {"gap_assessment", "acquisition_wait", "synthesis", "cancelled", "failed"},
    "acquisition_wait": {"synthesis", "paused", "cancelled", "failed"},
    "synthesis": {"verification", "cancelled", "failed"},
    "verification": {"revision", "completed", "cancelled", "failed"},
    "revision": {"verification", "completed", "cancelled", "failed"},
    "paused": {"intake", "cancelled", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}


@dataclass
class ResearchStateMachine:
    phase: str = "queued"

    def transition(self, target: str) -> str:
        allowed = TRANSITIONS.get(self.phase)
        if allowed is None or target not in allowed:
            raise InvalidResearchTransition(f"invalid research transition: {self.phase} -> {target}")
        self.phase = target
        return target
