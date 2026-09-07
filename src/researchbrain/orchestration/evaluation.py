from __future__ import annotations

import hashlib
import re
from statistics import mean
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


def summarize_quality_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate fixed-set quality, timing, and model/tool-call baselines by implementation."""
    implementation_names = sorted({str(value.get("implementation") or "unknown") for value in results})
    case_ids = {str((value.get("case") or {}).get("id") or "") for value in results if value.get("case")}
    return {
        "case_count": len(case_ids),
        "result_count": len(results),
        "implementations": {
            implementation: _summarize_implementation(
                [
                    value
                    for value in results
                    if str(value.get("implementation") or "unknown") == implementation
                ]
            )
            for implementation in implementation_names
        },
    }


def summarize_subagent_ab_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize fixed-quality-set A/B benefit and cost for subagent-enabled runs."""
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for result in results:
        case_id = str((result.get("case") or {}).get("id") or "")
        variant = str(result.get("variant") or result.get("implementation") or "")
        if case_id and variant in {"subagent_off", "subagent_on"}:
            by_case.setdefault(case_id, {})[variant] = result
    pairs = []
    coverage_deltas: list[float] = []
    score_deltas: list[float] = []
    tool_call_deltas: list[int] = []
    model_step_deltas: list[int] = []
    for case_id in sorted(by_case):
        pair = by_case[case_id]
        if {"subagent_off", "subagent_on"} - set(pair):
            continue
        off = pair["subagent_off"]
        on = pair["subagent_on"]
        off_score = off.get("score") or {}
        on_score = on.get("score") or {}
        coverage_delta = _coverage_ratio(on_score) - _coverage_ratio(off_score)
        citation_delta = float(on_score.get("citation_id_valid_ratio", 1.0)) - float(
            off_score.get("citation_id_valid_ratio", 1.0)
        )
        tool_delta = int((on.get("run_metrics") or {}).get("tool_calls") or 0) - int(
            (off.get("run_metrics") or {}).get("tool_calls") or 0
        )
        model_delta = int((on.get("run_metrics") or {}).get("model_steps") or 0) - int(
            (off.get("run_metrics") or {}).get("model_steps") or 0
        )
        score_delta = coverage_delta + citation_delta
        coverage_deltas.append(coverage_delta)
        score_deltas.append(score_delta)
        tool_call_deltas.append(tool_delta)
        model_step_deltas.append(model_delta)
        pairs.append(
            {
                "case_id": case_id,
                "coverage_delta": coverage_delta,
                "citation_delta": citation_delta,
                "score_delta": score_delta,
                "tool_call_delta": tool_delta,
                "model_step_delta": model_delta,
                "subagent_findings": len((on.get("run_metrics") or {}).get("scout_findings") or []),
            }
        )
    return {
        "pair_count": len(pairs),
        "pairs": pairs,
        "coverage_delta_mean": mean(coverage_deltas) if coverage_deltas else None,
        "score_delta_mean": mean(score_deltas) if score_deltas else None,
        "tool_call_delta_mean": mean(tool_call_deltas) if tool_call_deltas else None,
        "model_step_delta_mean": mean(model_step_deltas) if model_step_deltas else None,
        "benefit_positive_cases": sum(1 for value in score_deltas if value > 0),
        "cost_increased_cases": sum(
            1
            for model_delta, tool_delta in zip(model_step_deltas, tool_call_deltas, strict=True)
            if model_delta > 0 or tool_delta > 0
        ),
    }


def build_blind_review_packet(
    results: list[dict[str, Any]], seed: str = "researchbrain-v1-v2"
) -> dict[str, Any]:
    """Create deterministic same-question blind-review pairs without exposing implementation labels."""
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for result in results:
        case_id = str((result.get("case") or {}).get("id") or "")
        implementation = str(result.get("implementation") or "")
        valid_pair_result = result.get("status") in {"completed", "failed"}
        if not case_id or implementation not in {"v1", "v2"} or not valid_pair_result:
            continue
        by_case.setdefault(case_id, {})[implementation] = result

    pairs = []
    hidden_key = []
    for case_id in sorted(by_case):
        values = by_case[case_id]
        if {"v1", "v2"} - set(values):
            continue
        swap = _stable_bool(f"{seed}:{case_id}")
        label_to_impl = {"A": "v2" if swap else "v1", "B": "v1" if swap else "v2"}
        case = values["v1"].get("case") or values["v2"].get("case") or {}
        pairs.append(
            {
                "case_id": case_id,
                "question": case.get("question", ""),
                "mode": case.get("mode", ""),
                "checks": list(case.get("checks") or []),
                "answers": {
                    label: _blind_answer_payload(values[implementation])
                    for label, implementation in label_to_impl.items()
                },
            }
        )
        hidden_key.append({"case_id": case_id, "A": label_to_impl["A"], "B": label_to_impl["B"]})
    return {"seed": seed, "pair_count": len(pairs), "pairs": pairs, "hidden_key": hidden_key}


def _summarize_implementation(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [value for value in results if value.get("status") == "completed"]
    scores = [value.get("score") or {} for value in completed]
    coverage_ratios = [
        float((score.get("coverage") or {}).get("ratio"))
        for score in scores
        if (score.get("coverage") or {}).get("ratio") is not None
    ]
    elapsed_values = [
        int(value.get("elapsed_ms") or 0) for value in completed if value.get("elapsed_ms") is not None
    ]
    model_steps = [
        int((value.get("run_metrics") or {}).get("model_steps") or 0)
        for value in completed
        if (value.get("run_metrics") or {}).get("model_steps") is not None
    ]
    tool_calls = [
        int((value.get("run_metrics") or {}).get("tool_calls") or 0)
        for value in completed
        if (value.get("run_metrics") or {}).get("tool_calls") is not None
    ]
    return {
        "result_count": len(results),
        "completed": len(completed),
        "failed_or_error": len(results) - len(completed),
        "coverage_ratio_mean": mean(coverage_ratios) if coverage_ratios else None,
        "citation_id_valid_rate": _rate([bool(score.get("citation_id_valid")) for score in scores]),
        "citation_id_valid_ratio_mean": mean(
            [float(score.get("citation_id_valid_ratio", 1.0)) for score in scores]
        )
        if scores
        else None,
        "visible_answer_rate": _rate([bool(score.get("has_visible_answer")) for score in scores]),
        "topic_relevance_rate": _rate([bool(score.get("topic_relevance")) for score in scores]),
        "elapsed_ms_mean": mean(elapsed_values) if elapsed_values else None,
        "elapsed_ms_total": sum(elapsed_values),
        "model_steps_mean": mean(model_steps) if model_steps else None,
        "model_steps_total": sum(model_steps),
        "tool_calls_mean": mean(tool_calls) if tool_calls else None,
        "tool_calls_total": sum(tool_calls),
    }


def _blind_answer_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "limitations": result.get("limitations", []),
        "score": result.get("score", {}),
    }


def _coverage_ratio(score: dict[str, Any]) -> float:
    value = (score.get("coverage") or {}).get("ratio")
    return float(value) if value is not None else 0.0


def _rate(values: list[bool]) -> float | None:
    return (sum(1 for value in values if value) / len(values)) if values else None


def _stable_bool(value: str) -> bool:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:2], 16) % 2 == 0
