from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn

from researchbrain.config import Settings
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.documents.parsers import FallbackParser, MinerUParser, PyMuPDFParser
from researchbrain.documents.service import DocumentPipeline
from researchbrain.fulltext.discovery import (
    MultiSourceFullTextProvider,
    OpenAlexFullTextProvider,
    PmcFullTextProvider,
    UnpaywallProvider,
)
from researchbrain.fulltext.service import FullTextPipeline
from researchbrain.fulltext.storage import ObjectStore
from researchbrain.jobs.worker import JobWorker
from researchbrain.metadata.crossref import CrossrefProvider
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.minimax import MiniMaxEmbedder
from researchbrain.retrieval.service import EmbeddingPipeline
from researchbrain.secrets import SecretStore
from researchbrain.zotero.attachments import ZoteroAttachmentImporter
from researchbrain.zotero.client import ZoteroLocalClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researchbrain")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create data directories and the SQLite schema.")
    serve = subparsers.add_parser("serve", help="Run the local FastAPI service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    worker = subparsers.add_parser("worker", help="Run queued metadata jobs.")
    worker.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    subparsers.add_parser("mcp", help="Run the ResearchBrain MCP server over stdio.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    if args.command == "init":
        settings.ensure_directories()
        database = Database(settings.database_url)
        upgrade_schema(settings)
        database.engine.dispose()
        print(json.dumps({"status": "initialized", "data_dir": str(settings.data_dir)}, ensure_ascii=False))
        return 0
    if args.command == "serve":
        uvicorn.run("researchbrain.api.app:app", host=args.host, port=args.port, reload=False)
        return 0
    if args.command == "worker":
        settings.ensure_directories()
        database = Database(settings.database_url)
        upgrade_schema(settings)
        provider = CrossrefProvider(settings.crossref_base_url, settings.contact_email)
        fulltext = FullTextPipeline(
            database,
            MultiSourceFullTextProvider(
                [
                    UnpaywallProvider(settings.unpaywall_base_url, settings.contact_email),
                    OpenAlexFullTextProvider(
                        settings.contact_email,
                        SecretStore().get("openalex_api_key"),
                    ),
                    PmcFullTextProvider(
                        settings.contact_email,
                        SecretStore().get("ncbi_api_key"),
                    ),
                ]
            ),
            ObjectStore(settings.data_dir, settings.max_download_mb),
        )
        documents = DocumentPipeline(
            database,
            settings.data_dir,
            FallbackParser(
                MinerUParser(
                    settings.mineru_executable,
                    settings.mineru_backend,
                    settings.mineru_version,
                ),
                PyMuPDFParser(),
            ),
        )
        embedder = MiniMaxEmbedder(
            SecretStore().get("minimax_api_key"),
            settings.minimax_embedding_url,
            settings.minimax_group_id,
            settings.minimax_embedding_model,
            settings.minimax_embedding_dimensions,
        )
        embeddings = EmbeddingPipeline(
            database,
            settings.data_dir,
            embedder,
            LanceIndex(
                settings.data_dir / "data" / "lancedb",
                embedder.model,
                embedder.dimensions,
            ),
        )

        async def run_worker() -> None:
            while True:
                job = await JobWorker(
                    database,
                    provider,
                    ZoteroLocalClient(),
                    fulltext,
                    documents,
                    embeddings,
                    ZoteroAttachmentImporter(
                        database,
                        settings.data_dir,
                        settings.zotero_data_dir,
                        settings.max_download_mb,
                    ),
                ).run_one()
                if args.once:
                    break
                await asyncio.sleep(0.05 if job else settings.worker_poll_seconds)

        asyncio.run(run_worker())
        database.engine.dispose()
        return 0
    if args.command == "mcp":
        from researchbrain.mcp_server import main as run_mcp

        run_mcp()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
