from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from researchbrain.tools import ResearchBrainTools

mcp = FastMCP("ResearchBrain")
tools = ResearchBrainTools()


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
ONLINE_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE_QUEUE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


@mcp.tool(annotations=READ_ONLY)
def list_libraries() -> list[dict]:
    """List local ResearchBrain libraries and their IDs."""
    return tools.list_libraries()


@mcp.tool(annotations=READ_ONLY)
def get_research_context() -> dict:
    """Return the Harness-selected default library, all available libraries, and data policy."""
    return tools.get_research_context()


@mcp.tool(annotations=READ_ONLY)
def library_status(library_id: str) -> dict:
    """Return item, PDF, parsed-document, and full-text-index counts for one library."""
    return tools.library_status(library_id)


@mcp.tool(annotations=READ_ONLY)
def get_item(item_id: str) -> dict:
    """Return one literature item with identifiers, authors, and bibliographic metadata."""
    return tools.get_item(item_id)


@mcp.tool(annotations=READ_ONLY)
def item_status(item_id: str) -> dict:
    """Report whether one item has DOI metadata, a stored PDF, parsed text, and current vectors."""
    return tools.item_status(item_id)


@mcp.tool(annotations=READ_ONLY)
async def search_library(library_id: str, query: str, limit: int = 10) -> list[dict]:
    """Hybrid-search local full text and return page-grounded evidence chunks."""
    return await tools.search_library(library_id, query, limit)


@mcp.tool(annotations=READ_ONLY)
async def ask_library(library_id: str, question: str, limit: int = 10) -> dict:
    """Answer from local evidence with validated citations and explicit limitations."""
    return await tools.ask_library(library_id, question, limit)


@mcp.tool(annotations=ONLINE_READ)
async def search_online(
    query: str,
    sources: list[str] | None = None,
    limit_per_source: int = 5,
) -> dict:
    """Search Crossref, OpenAlex, arXiv, and PubMed metadata; results are not full-text evidence."""
    return await tools.search_online(query, sources, limit_per_source)


@mcp.tool(annotations=WRITE_QUEUE)
def import_dois(library_id: str, dois: list[str], include_si: bool = False) -> dict:
    """Queue DOI metadata import and lawful open-full-text processing into a library."""
    return tools.import_dois(library_id, dois, include_si)


@mcp.tool(annotations=WRITE_QUEUE)
def sync_zotero(library_id: str) -> dict:
    """Queue an incremental Zotero metadata and local-PDF synchronization for a mirror library."""
    return tools.sync_zotero(library_id)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
def queue_library_index(library_id: str) -> dict:
    """Queue missing metadata and parsed-full-text embeddings for one library."""
    return tools.queue_library_index(library_id)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
def queue_fulltext(item_id: str, include_si: bool = False) -> dict:
    """Queue lawful open-access PDF discovery, parsing, and downstream embedding for an item."""
    return tools.queue_fulltext(item_id, include_si)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
    )
)
async def attach_local_pdf(item_id: str, pdf_path: str) -> dict:
    """Attach one user-selected local PDF, deduplicate it, and queue parsing and embedding."""
    return await tools.attach_local_pdf(item_id, pdf_path)


@mcp.tool(annotations=READ_ONLY)
def list_jobs(limit: int = 50) -> list[dict]:
    """List recent import, PDF, parsing, and embedding jobs with their actual status."""
    return tools.list_jobs(limit)


@mcp.tool(annotations=READ_ONLY)
def export_references(item_ids: list[str], output_format: str = "csl-json") -> dict:
    """Export selected references as csl-json, bibtex, ris, doi, or markdown."""
    return tools.export_references(item_ids, output_format)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
