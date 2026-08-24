---
name: researchbrain-zotero-sync
description: Incrementally synchronize a local Zotero library into a ResearchBrain mirror, including metadata changes, deletions, linked PDF attachments, parsing, and downstream indexing. Use when Codex is asked to import newly added Zotero papers, sync only changes since the last run, diagnose missing Zotero items or PDFs, or audit synchronization coverage.
---

# ResearchBrain Zotero Sync

Use the `mcp__researchbrain__*` tools. Zotero must be running with its local API available.

## Workflow

1. Call `get_research_context` and select a library whose mode is `zotero_mirror`.
2. Call `library_status` and record the library `last_version` from `list_libraries`.
3. State that an incremental synchronization will be queued, then call `sync_zotero`.
4. Call `list_jobs` and follow the returned `zotero_sync` job to a terminal state.
5. Read the job result: previous and current Zotero versions, created and updated items, linked/imported/missing/invalid attachments, and tombstones.
6. After synchronization completes, call `queue_library_index`, then inspect downstream parse and embedding jobs.
7. Call `library_status` again and report the delta.

## Rules

- Do not read or modify `zotero.sqlite` directly.
- Do not reset the Zotero version watermark for a normal update; the sync is incremental by design.
- Treat an already queued or running sync as the same operation, not a second sync.
- Do not claim PDF, Markdown, or vectors are ready until their jobs complete.
- Surface `zotero_unavailable`, missing attachment paths, invalid PDFs, and embedding failures separately.

