---
name: researchbrain-evidence-research
description: Conduct evidence-grounded literature research by having Codex directly search and synthesize ResearchBrain full-text evidence, then extend coverage with Crossref, OpenAlex, arXiv, and PubMed metadata. Use for literature reviews, method and dataset comparisons, research-gap analysis, DOI-backed reports, paper-specific questions, and local-plus-online academic investigations.
---

# ResearchBrain Evidence Research

Use the `mcp__researchbrain__*` tools for retrieval. Perform synthesis in Codex; do not delegate the main answer to `ask_library` by default.

## Workflow

1. Call `get_research_context` and `library_status`.
2. Decompose broad questions into focused searches covering concepts, data, methods, results, limitations, and competing explanations.
3. Call `search_library` for each focused query. Use `get_item` to recover complete bibliographic metadata.
4. Expand queries with terminology found in strong local evidence. Prefer page-grounded full-text chunks over title/abstract records.
5. When current or broader coverage is needed, call `search_online` across appropriate sources. Treat those records as metadata or abstract evidence unless full text is subsequently acquired.
6. Cross-check DOI, title, year, and journal before merging duplicate records.
7. Synthesize the answer in Codex. Separate reported findings, cross-paper synthesis, inference, proposed work, and unresolved uncertainty.
8. Offer DOI import through `$researchbrain-doi-fulltext` for relevant online records not yet in the library.

## Evidence Rules

- Cite evidence IDs immediately after local factual claims and include DOI when available.
- Never claim to have inspected figures, equations, pages, detailed methods, or results from metadata alone.
- For a substantial report, compare data, preprocessing, methods, baselines, improvements, results, figure meaning, limitations, and reproducible next steps.
- State search sources, date range, query gaps, provider failures, and local coverage limitations.
- Do not use prior generated answers as evidence; use them only as query-planning context and revalidate claims against literature.

