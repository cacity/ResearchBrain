from __future__ import annotations

from dataclasses import asdict

from researchbrain.agent.deepseek import DeepSeekClient
from researchbrain.agent.service import ResearchAgent
from researchbrain.citations.export import CitationExporter
from researchbrain.config import Settings, UserConfigStore
from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.library.repository import LibraryRepository
from researchbrain.retrieval.index import LanceIndex
from researchbrain.retrieval.minimax import MiniMaxEmbedder
from researchbrain.retrieval.service import EmbeddingPipeline
from researchbrain.secrets import SecretStore


class ResearchBrainTools:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_directories()
        upgrade_schema(self.settings)
        self.database = Database(self.settings.database_url)
        self.user_config = UserConfigStore(self.settings.data_dir).load()

    def close(self) -> None:
        self.database.engine.dispose()

    def list_libraries(self) -> list[dict]:
        with self.database.session() as session:
            return [
                {
                    "id": library.id,
                    "name": library.name,
                    "mode": library.mode,
                    "last_version": library.last_version,
                }
                for library in LibraryRepository(session).list_libraries()
            ]

    def get_item(self, item_id: str) -> dict:
        with self.database.session() as session:
            item = LibraryRepository(session).get_item(item_id)
            if not item:
                raise ValueError("item not found")
            return {
                "id": item.id,
                "library_id": item.library_id,
                "type": item.item_type,
                "title": item.title,
                "abstract": item.abstract,
                "year": item.year,
                "container_title": item.container_title,
                "volume": item.volume,
                "issue": item.issue,
                "pages": item.pages,
                "url": item.url,
                "identifiers": {
                    identifier.scheme: identifier.normalized_value for identifier in item.identifiers
                },
                "creators": [
                    {
                        "given": link.creator.given,
                        "family": link.creator.family,
                        "literal": link.creator.literal,
                        "role": link.role,
                    }
                    for link in sorted(item.creators, key=lambda value: value.position)
                ],
            }

    async def search_library(self, library_id: str, query: str, limit: int = 10) -> list[dict]:
        hits = await self._retrieval().search(query, library_id, max(1, min(limit, 50)))
        return [asdict(hit) for hit in hits]

    async def ask_library(self, library_id: str, question: str, limit: int = 10) -> dict:
        generator = DeepSeekClient(
            SecretStore().get("deepseek_api_key"),
            self.settings.deepseek_base_url,
            self.settings.deepseek_model,
        )
        answer = await ResearchAgent(self._retrieval(), generator).answer(
            library_id,
            question,
            max(1, min(limit, 20)),
        )
        return {
            "answer": answer.answer,
            "citation_ids": answer.citation_ids,
            "evidence": [asdict(value) for value in answer.evidence],
            "limitations": answer.limitations,
            "model": answer.model,
        }

    def export_references(self, item_ids: list[str], output_format: str) -> dict:
        with self.database.session() as session:
            artifact = CitationExporter(session).export(item_ids, output_format)
            return asdict(artifact)

    def _retrieval(self) -> EmbeddingPipeline:
        embedder = MiniMaxEmbedder(
            SecretStore().get("minimax_api_key"),
            self.settings.minimax_embedding_url,
            str(self.user_config.get("minimax_group_id") or self.settings.minimax_group_id),
            self.settings.minimax_embedding_model,
            self.settings.minimax_embedding_dimensions,
        )
        index = LanceIndex(
            self.settings.data_dir / "data" / "lancedb",
            embedder.model,
            embedder.dimensions,
        )
        return EmbeddingPipeline(self.database, self.settings.data_dir, embedder, index)
