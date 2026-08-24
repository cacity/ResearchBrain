---
name: researchbrain-vector-index
description: Build, complete, and audit ResearchBrain vector indexes for bibliographic metadata, abstracts, and parsed PDF full text. Use when Codex is asked to vectorize a library, repair missing embeddings, check index coverage, process only incremental gaps, or verify that literature is searchable.
---

# ResearchBrain Vector Index

Use the `mcp__researchbrain__*` tools. Treat database and job responses as the source of truth.

## Workflow

1. Call `get_research_context`. Resolve the target library by ID; do not guess when several libraries match.
2. Call `library_status` and record the baseline counts.
3. Call `queue_library_index`. This queues only missing metadata and parsed-full-text embeddings.
4. Call `list_jobs`. Match returned job IDs and inspect `embed_metadata` and `embed_document` jobs.
5. Report `queued`, `running`, `retry_wait`, `review_required`, `failed`, and `complete` distinctly.
6. Call `library_status` again after terminal jobs and compare coverage.

## Rules

- Never claim an index is ready because a job was queued.
- Treat `api_key_missing`, `authentication_failed`, and `dimension_mismatch` as configuration blockers.
- If PDF count exceeds parsed count, route the missing items through `$researchbrain-pdf-ingest`.
- Do not re-embed items already reported as current unless the user explicitly requests a rebuild or model migration.
- Report the embedding model, item count, PDF count, parsed count, full-text-indexed count, pending jobs, and failures.

