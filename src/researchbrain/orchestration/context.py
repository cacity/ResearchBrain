from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from researchbrain.agent.service import ConversationTurn

_IDENTIFIER_RE = re.compile(
    r"(?:10\.\d{4,9}/\S+)|(?:PMID\s*:?\s*\d+)|(?:arXiv\s*:?\s*\d{4}\.\d{4,5}(?:v\d+)?)",
    re.IGNORECASE,
)
_WORD_OR_CJK_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class ResearchContext:
    history: list[ConversationTurn]
    memory: dict[str, Any]


def transform_context(
    history: list[ConversationTurn],
    memory: dict[str, Any],
    *,
    message_limit: int = 8,
    token_budget: int = 6000,
    checkpoint_threshold: float = 0.8,
    tokenizer_name: str = "researchbrain-regex-token-estimator-v1",
) -> ResearchContext:
    """Prune provider context and preserve prior answers only as retrieval hypotheses.

    The compaction gate is token-budget based instead of message-count based.  It
    keeps hard user constraints and DOI/PMID/arXiv identifiers outside lossy
    summaries so follow-up questions must re-open the original evidence rather
    than cite compressed history as evidence.
    """
    raw_messages = [value for value in history if value.content.strip()]
    budget = max(256, token_budget)
    preserved_identifiers = _preserve_identifiers(memory, raw_messages)
    hard_constraints = _list(memory.get("constraints"), 50, 500)
    transformed_history: list[ConversationTurn] = []
    used_tokens = _estimate_tokens(" ".join(hard_constraints + preserved_identifiers))
    for value in reversed(raw_messages[-message_limit:]):
        compact_content = " ".join(value.content.split())
        message_tokens = _estimate_tokens(compact_content)
        if transformed_history and used_tokens + message_tokens > budget:
            break
        if message_tokens > budget // 2:
            compact_content = _truncate_by_tokens(compact_content, max(128, budget // 2))
            message_tokens = _estimate_tokens(compact_content)
        transformed_history.append(ConversationTurn(role=value.role, content=compact_content[:4000]))
        used_tokens += message_tokens
    transformed_history.reverse()

    prior_hypotheses = _remove_conflicting_hypotheses(
        _list(memory.get("supported_findings"), 8, 1000),
        _list(memory.get("invalidated_hypotheses"), 20, 300)
        + _list(memory.get("conflicting_evidence_identifiers"), 20, 200),
    )
    checkpoint = _build_compaction_checkpoint(
        raw_messages,
        transformed_history,
        memory,
        tokenizer_name,
        token_budget=budget,
        estimated_tokens=used_tokens,
        threshold=checkpoint_threshold,
        preserved_identifiers=preserved_identifiers,
        hard_constraints=hard_constraints,
    )
    transformed_memory = {
        "goal": _text(memory.get("goal"), 1000),
        "constraints": hard_constraints[:12],
        "terminology": _list(memory.get("terminology"), 20, 120),
        "prior_answer_hypotheses": prior_hypotheses,
        "source_identifiers": preserved_identifiers,
        "unresolved_questions": _list(memory.get("unresolved_questions"), 12, 500),
        "evidence_policy": "navigation_only_zero_evidentiary_weight",
        "summary_is_evidence": False,
        "must_reread_original_evidence": True,
        "tokenizer": tokenizer_name,
        "estimated_context_tokens": used_tokens,
    }
    if checkpoint:
        transformed_memory["compaction_checkpoint"] = checkpoint
    return ResearchContext(transformed_history, transformed_memory)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Regex tokenization is deterministic and cheap: English/number spans count
    # as roughly BPE-sized groups, each CJK character counts as one token.
    total = 0
    for token in _WORD_OR_CJK_RE.findall(text):
        if len(token) == 1 and "\u4e00" <= token <= "\u9fff":
            total += 1
        else:
            total += max(1, (len(token) + 3) // 4)
    return total


def _truncate_by_tokens(text: str, token_limit: int) -> str:
    used = 0
    pieces: list[str] = []
    for part in re.split(r"(\s+)", text):
        cost = _estimate_tokens(part)
        if pieces and used + cost > token_limit:
            break
        pieces.append(part)
        used += cost
    return "".join(pieces).strip()


def _preserve_identifiers(memory: dict[str, Any], messages: list[ConversationTurn]) -> list[str]:
    identifiers = _list(memory.get("source_identifiers"), 200, 300)
    for message in messages:
        identifiers.extend(
            match.group(0).rstrip(".,;:)]}") for match in _IDENTIFIER_RE.finditer(message.content)
        )
    return list(dict.fromkeys(identifiers))


def _build_compaction_checkpoint(
    raw_messages: list[ConversationTurn],
    kept_messages: list[ConversationTurn],
    memory: dict[str, Any],
    tokenizer_name: str,
    *,
    token_budget: int,
    estimated_tokens: int,
    threshold: float,
    preserved_identifiers: list[str],
    hard_constraints: list[str],
) -> dict[str, Any] | None:
    raw_tokens = sum(_estimate_tokens(value.content) for value in raw_messages)
    threshold_tokens = int(token_budget * threshold)
    dropped_messages = max(0, len(raw_messages) - len(kept_messages))
    if raw_tokens < threshold_tokens and not dropped_messages:
        return None
    boundary = kept_messages[0].content[:120] if kept_messages else ""
    summary_source = "\n".join(value.content for value in raw_messages[:dropped_messages])
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "tokenizer": tokenizer_name,
        "generator_model": str(memory.get("summary_model") or "deterministic-context-compactor"),
        "generator_version": str(memory.get("summary_model_version") or "v1"),
        "estimated_tokens_before": raw_tokens,
        "estimated_tokens_after": estimated_tokens,
        "coverage_boundary": {
            "kept_recent_messages": len(kept_messages),
            "dropped_older_messages": dropped_messages,
            "oldest_kept_preview": boundary,
        },
        "summary": _truncate_by_tokens(summary_source, 500),
        "preserved_constraints": hard_constraints,
        "preserved_identifiers": preserved_identifiers,
        "summary_is_evidence": False,
        "must_reread_original_evidence": True,
        "content_hash": hashlib.sha256(summary_source.encode()).hexdigest() if summary_source else "",
    }


def _remove_conflicting_hypotheses(hypotheses: list[str], invalidators: list[str]) -> list[str]:
    if not invalidators:
        return hypotheses
    lowered = [value.casefold() for value in invalidators if value]
    return [
        hypothesis
        for hypothesis in hypotheses
        if not any(value in hypothesis.casefold() for value in lowered)
    ]


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _list(value: Any, count: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:count] if (text := _text(item, item_limit))]
