---
name: researchbrain-literature
description: >-
  Use ResearchBrain local libraries and academic discovery tools for
  evidence-grounded literature research.
whenToUse: >-
  Use for literature searches, research reviews, paper comparisons, DOI
  imports, full-text retrieval, and evidence-backed research planning.
user-invocable: true
disable-model-invocation: false
metadata:
  product: ResearchBrain
  evidence_policy: strict
---

# ResearchBrain Literature Research

Use the `mcp__researchbrain__*` tools as the source of truth for the user's literature library.

## Required workflow

1. Call `get_research_context` first. Use its default library unless the user explicitly names another.
2. Break broad questions into focused searches. Search the local library before searching online.
3. Use `search_library` for evidence chunks and `get_item` for complete bibliographic metadata.
4. Use `search_online` only when the user asks for current coverage, external
   literature, or the local evidence is insufficient.
5. Distinguish full-text evidence from title/abstract metadata. Never claim to
   have read figures, equations, pages, detailed methods, or results from
   metadata alone.
6. Cite local evidence IDs and online source identifiers immediately after
   factual claims. Include DOI when available.
7. Separate reported findings, cross-paper synthesis, inference, and proposed
   work. State evidence limitations.
8. Before `import_dois` or `queue_fulltext`, state exactly what will be added or
   queued and obtain approval when the active permission policy requires it.
9. After a write action, call `list_jobs` and report the queued job or batch
   identifiers. Do not claim that PDF parsing or embedding is complete until job
   status confirms it.

## Review output

For a substantial review, organize the answer by research question rather than
paper order. Compare data, methods, processing steps, findings, disagreements,
limitations, and actionable research gaps. Use compact Markdown tables where
they improve comparison.
