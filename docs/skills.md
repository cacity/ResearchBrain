# ResearchBrain Codex Skills

ResearchBrain separates deterministic literature data pipelines from agent reasoning. The local application owns
SQLite, LanceDB, Zotero synchronization, PDF storage, parsing, embeddings, job state, and academic discovery.
Codex Skills own task selection, sequencing, evidence rules, status verification, and final synthesis.

## Skill suite

| Skill                             | Responsibility                                                    |
| --------------------------------- | ----------------------------------------------------------------- |
| `researchbrain-zotero-sync`       | Incrementally mirror Zotero metadata and local PDF attachments.   |
| `researchbrain-doi-fulltext`      | Import DOI metadata and retrieve lawful open PDFs.                |
| `researchbrain-pdf-ingest`        | Deduplicate PDFs, parse to Markdown, and queue full-text vectors. |
| `researchbrain-vector-index`      | Audit and complete metadata and full-text vector coverage.        |
| `researchbrain-evidence-research` | Let Codex synthesize local evidence and verified online records.  |

The first four Skills are deterministic pipeline operators. The evidence-research Skill deliberately uses
`search_library` and `get_item` directly so Codex performs the synthesis; `ask_library` is not the default path.

## Install for Codex

Register the ResearchBrain MCP server, then install the Skills:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_codex_mcp.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_codex_skills.ps1
```

Restart Codex after installation. The source remains under `.agents/skills`; the installer copies only the five
public Skill folders and never copies databases, PDFs, credentials, logs, or model caches.

## Independent repository boundary

A future standalone repository can contain the five Skill folders, the non-destructive installer, MCP contract
tests, and examples. Keep the database engine in ResearchBrain and depend on the `researchbrain` MCP server by
tool name. Version the MCP contract independently and test required tool availability before each workflow.

Do not move provider credentials into Skill files. Do not let Skills infer job completion from queue creation.

## ResearchBrain Skill manager

The desktop **Skills** view also manages filesystem Skills for DeepSeek Harness. It accepts:

- a local directory containing one `SKILL.md`;
- a local ZIP archive;
- an HTTPS GitHub repository, with optional ref and subpath.

Managed copies live under `%LOCALAPPDATA%\ResearchBrain\skills\packages\<name>\<sha256>`. The JSON registry
records source, hash, dependencies, permissions, compatibility, and enable state. Enabled compatible Skills are
copied to `%LOCALAPPDATA%\ResearchBrain\harness\workspace\.agents\skills` when Harness starts. ResearchBrain
tracks only its own deployed names and does not delete unrelated folders from that workspace.

Installation rejects unsafe archive paths, symbolic links, invalid or missing frontmatter, duplicate built-in
names, more than 2,000 files, and packages larger than 200 MB after extraction. GitHub downloads are restricted
to `https://github.com`. A Skill that includes scripts is marked `review_required`; a Skill needing an MCP server
other than `researchbrain` is marked `needs_configuration`; unsupported dependency types are `incompatible`.
Enabling a Skill never executes it. Execution occurs only after the user starts Harness and invokes the Skill.
The backend remains the authority for item identity, SHA-256 deduplication, Zotero watermarks, and pipeline state.
