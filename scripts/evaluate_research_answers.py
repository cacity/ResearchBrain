from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from researchbrain.orchestration.evaluation import score_research_result


def request(client: httpx.Client, method: str, path: str, **kwargs) -> Any:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def run_v2(client: httpx.Client, library_id: str, case: dict[str, Any]) -> dict[str, Any]:
    session = request(
        client,
        "POST",
        "/chat/sessions",
        json={"library_id": library_id, "title": f"Evaluation: {case['id']}"},
    )
    started = time.monotonic()
    run = request(
        client,
        "POST",
        f"/chat/sessions/{session['id']}/runs",
        json={"content": case["question"], "mode": case["mode"], "evidence_limit": 20},
    )
    deadline = time.monotonic() + 300
    while run["status"] not in {"completed", "failed", "cancelled"}:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"case {case['id']} exceeded 300 seconds")
        time.sleep(0.5)
        run = request(client, "GET", f"/research/runs/{run['id']}")
    messages = request(client, "GET", f"/chat/sessions/{session['id']}/messages")
    assistant = next((value for value in reversed(messages) if value["role"] == "assistant"), {})
    score = score_research_result(
        str(assistant.get("content") or ""),
        list(assistant.get("citations") or []),
        list(run.get("coverage") or []),
    )
    return {
        "case": case,
        "implementation": "v2",
        "run_id": run["id"],
        "status": run["status"],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "answer": assistant.get("content", ""),
        "citations": assistant.get("citations", []),
        "limitations": run.get("limitations", []),
        "run_metrics": run.get("metrics", {}),
        "score": score,
    }


def run_v1(client: httpx.Client, library_id: str, case: dict[str, Any]) -> dict[str, Any]:
    session = request(
        client,
        "POST",
        "/chat/sessions",
        json={"library_id": library_id, "title": f"V1 evaluation: {case['id']}"},
    )
    started = time.monotonic()
    assistant = request(
        client,
        "POST",
        f"/chat/sessions/{session['id']}/messages",
        json={"content": case["question"], "mode": case["mode"], "evidence_limit": 20},
    )
    return {
        "case": case,
        "implementation": "v1",
        "status": "completed",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "answer": assistant.get("content", ""),
        "citations": assistant.get("citations", []),
        "limitations": assistant.get("limitations", []),
        "run_metrics": {},
        "score": score_research_result(
            str(assistant.get("content") or ""),
            list(assistant.get("citations") or []),
            [],
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed ResearchBrain answer-quality set")
    parser.add_argument("--library-id", required=True)
    parser.add_argument("--empty-library-id")
    parser.add_argument("--api", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).parents[1] / "evaluation" / "research_quality_cases.json",
    )
    parser.add_argument("--output", type=Path, default=Path("research-quality-results.json"))
    parser.add_argument(
        "--implementation",
        choices=["v1", "v2", "both"],
        default="both",
        help="Run the legacy answer path, the orchestrator, or both for comparison",
    )
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    results = []
    with httpx.Client(base_url=args.api, headers=headers, timeout=60) as client:
        for case in cases:
            library_id = args.empty_library_id if case.get("library") == "empty" else args.library_id
            if not library_id:
                results.append({"case": case, "status": "skipped", "reason": "empty library not set"})
                continue
            implementations = ["v1", "v2"] if args.implementation == "both" else [args.implementation]
            for implementation in implementations:
                try:
                    runner = run_v1 if implementation == "v1" else run_v2
                    results.append(runner(client, library_id, case))
                except Exception as exc:  # noqa: BLE001 - preserve each failed case in the report
                    results.append(
                        {
                            "case": case,
                            "implementation": implementation,
                            "status": "error",
                            "error": str(exc),
                        }
                    )
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results}
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
