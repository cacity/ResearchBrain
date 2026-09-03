from __future__ import annotations

import re
from typing import Any

_CITATION_RE = re.compile(r"\[([ELW]\d+)\]")


def score_research_result(
    answer: str,
    citations: list[dict[str, Any]],
    coverage: list[dict[str, Any]] | None = None,
    forbidden_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Return deterministic quality signals suitable for V1/V2 regression reports."""
    cited_in_text = list(dict.fromkeys(_CITATION_RE.findall(answer)))
    supplied_ids = {str(value.get("id") or "") for value in citations if str(value.get("id") or "")}
    invalid_ids = [value for value in cited_in_text if value not in supplied_ids]
    uncited_payload_ids = sorted(supplied_ids.difference(cited_in_text))
    coverage_items = coverage or []
    covered = sum(value.get("status") == "covered" for value in coverage_items)
    partial = sum(value.get("status") == "partial" for value in coverage_items)
    insufficient = sum(value.get("status") == "insufficient_evidence" for value in coverage_items)
    denominator = len(coverage_items)
    coverage_ratio = (covered + 0.5 * partial) / denominator if denominator else None
    topic_violations = [value for value in (forbidden_terms or []) if value.casefold() in answer.casefold()]
    return {
        "citation_id_valid": not invalid_ids,
        "citation_id_valid_ratio": (
            (len(cited_in_text) - len(invalid_ids)) / len(cited_in_text) if cited_in_text else 1.0
        ),
        "cited_in_text": cited_in_text,
        "invalid_citation_ids": invalid_ids,
        "uncited_payload_ids": uncited_payload_ids,
        "coverage": {
            "covered": covered,
            "partial": partial,
            "insufficient_evidence": insufficient,
            "ratio": coverage_ratio,
        },
        "has_visible_answer": bool(answer.strip()),
        "topic_relevance": not topic_violations,
        "topic_violations": topic_violations,
    }
