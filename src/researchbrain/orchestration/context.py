from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from researchbrain.agent.service import ConversationTurn


@dataclass(frozen=True)
class ResearchContext:
    history: list[ConversationTurn]
    memory: dict[str, Any]


def transform_context(
    history: list[ConversationTurn],
    memory: dict[str, Any],
    *,
    message_limit: int = 8,
) -> ResearchContext:
    """Prune provider context and preserve prior answers only as retrieval hypotheses."""
    transformed_history = [
        ConversationTurn(role=value.role, content=" ".join(value.content.split())[:1200])
        for value in history[-message_limit:]
        if value.content.strip()
    ]
    transformed_memory = {
        "goal": _text(memory.get("goal"), 1000),
        "constraints": _list(memory.get("constraints"), 12, 300),
        "terminology": _list(memory.get("terminology"), 20, 120),
        "prior_answer_hypotheses": _list(memory.get("supported_findings"), 5, 800),
        "source_identifiers": _list(memory.get("source_identifiers"), 50, 200),
        "unresolved_questions": _list(memory.get("unresolved_questions"), 12, 500),
        "evidence_policy": "navigation_only_zero_evidentiary_weight",
    }
    return ResearchContext(transformed_history, transformed_memory)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _list(value: Any, count: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:count] if (text := _text(item, item_limit))]
