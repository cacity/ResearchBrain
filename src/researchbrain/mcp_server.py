from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from researchbrain.tools import ResearchBrainTools

mcp = FastMCP("ResearchBrain")
tools = ResearchBrainTools()


@mcp.tool()
def list_libraries() -> list[dict]:
    """List local ResearchBrain libraries and their IDs."""
    return tools.list_libraries()


@mcp.tool()
def get_item(item_id: str) -> dict:
    """Return one literature item with identifiers, authors, and bibliographic metadata."""
    return tools.get_item(item_id)


@mcp.tool()
async def search_library(library_id: str, query: str, limit: int = 10) -> list[dict]:
    """Hybrid-search local full text and return page-grounded evidence chunks."""
    return await tools.search_library(library_id, query, limit)


@mcp.tool()
async def ask_library(library_id: str, question: str, limit: int = 10) -> dict:
    """Answer from local evidence with validated citations and explicit limitations."""
    return await tools.ask_library(library_id, question, limit)


@mcp.tool()
def export_references(item_ids: list[str], output_format: str = "csl-json") -> dict:
    """Export selected references as csl-json, bibtex, ris, doi, or markdown."""
    return tools.export_references(item_ids, output_format)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
