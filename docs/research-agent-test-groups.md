# Research Agent independent test groups

These pytest markers are the independent test groups for the remaining Research Agent implementation modules. Add new tests to the matching group as soon as work starts, and run the listed command before checking a module item as complete.

| Marker                        | Module / checklist scope                                                                                    | Command                                                             |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `research_baseline`           | Section 0 quality gates: fixed quality-set baselines and old-vs-new blind review harnesses                  | `.venv/Scripts/python.exe -m pytest -m research_baseline`           |
| `research_clarification`      | Section 1 resumable blocking ambiguity / `ask_user`                                                         | `.venv/Scripts/python.exe -m pytest -m research_clarification`      |
| `research_local_retrieval_ui` | Section 4 candidate inclusion/exclusion explanations in UI                                                  | `.venv/Scripts/python.exe -m pytest -m research_local_retrieval_ui` |
| `research_doi_pdf_loop`       | Section 8 controlled DOI/PDF import, parsing, embedding, approval, and retries                              | `.venv/Scripts/python.exe -m pytest -m research_doi_pdf_loop`       |
| `research_agent_action_loop`  | Section 12 `AgentAction -> tool_result -> next turn` loop, persistence, resume, and budgets                 | `.venv/Scripts/python.exe -m pytest -m research_agent_action_loop`  |
| `research_tool_policy_hooks`  | Section 13 `beforeToolCall` / `afterToolCall` policy chain, approval, idempotency, and injection handling   | `.venv/Scripts/python.exe -m pytest -m research_tool_policy_hooks`  |
| `research_steering_abort`     | Section 15 steering types, revalidation, cancellation, and SSE reconnect                                    | `.venv/Scripts/python.exe -m pytest -m research_steering_abort`     |
| `research_context_compaction` | Section 16 tokenizer budgeting, compaction checkpoints, memory boundaries, and recovery                     | `.venv/Scripts/python.exe -m pytest -m research_context_compaction` |
| `research_session_branching`  | Section 17 chat tree, branch inheritance, deletion/archive, concurrency, and history reads                  | `.venv/Scripts/python.exe -m pytest -m research_session_branching`  |
| `research_subagent_loop`      | Section 18 subagent action/tool loops, aggregation, cancellation, limits, and A/B validation                | `.venv/Scripts/python.exe -m pytest -m research_subagent_loop`      |
| `research_followup_recovery`  | Section 19 follow-up queue, checkpoints, retry resume, duplicate writes, and approval expiry                | `.venv/Scripts/python.exe -m pytest -m research_followup_recovery`  |
| `research_diagnostics_ui`     | Section 20 diagnostic UI timelines, approvals/tasks/follow-up/branching, export, and accessibility          | `.venv/Scripts/python.exe -m pytest -m research_diagnostics_ui`     |
| `research_release_acceptance` | Section 21 final acceptance: full test suites, real-model smoke tests, package validation, docs, and review | `.venv/Scripts/python.exe -m pytest -m research_release_acceptance` |

A focused implementation PR may run only its module marker plus directly affected tests, but release acceptance must still run the complete quality gate.
