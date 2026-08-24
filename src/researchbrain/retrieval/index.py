from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    item_id: str
    artifact_id: str
    title: str
    text: str
    section: str
    page_start: int | None
    page_end: int | None
    score: float
    vector_rank: int | None
    keyword_rank: int | None


class LanceIndex:
    def __init__(self, directory: Path, model: str, dimensions: int, index_version: str = "v1"):
        self.directory = directory
        self.model = model
        self.dimensions = dimensions
        self.index_version = index_version
        safe_model = re.sub(r"[^a-zA-Z0-9]+", "_", model).strip("_").lower()
        self.table_name = f"chunks_{safe_model}_{dimensions}_{index_version}"

    def upsert(self, artifact_id: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        table = self._table(create=True)
        table.delete(f"artifact_id = '{_escape_sql(artifact_id)}'")
        table.add(records)
        from lancedb.index import FTS

        table.create_index(
            "text",
            config=FTS(
                base_tokenizer="ngram",
                ngram_min_length=2,
                ngram_max_length=3,
                stem=False,
                remove_stop_words=False,
            ),
            replace=True,
        )

    def upsert_item_metadata(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        table = self._table(create=True)
        chunk_ids = ", ".join(f"'{_escape_sql(str(record['chunk_id']))}'" for record in records)
        table.delete(f"chunk_id IN ({chunk_ids})")
        table.add(records)
        from lancedb.index import FTS

        table.create_index(
            "text",
            config=FTS(
                base_tokenizer="ngram",
                ngram_min_length=2,
                ngram_max_length=3,
                stem=False,
                remove_stop_words=False,
            ),
            replace=True,
        )

    def bulk_upsert(self, artifact_ids: list[str], records: list[dict[str, Any]]) -> None:
        """Replace many artifact partitions while rebuilding the FTS index once."""
        if not artifact_ids or not records:
            return
        table = self._table(create=True)
        for start in range(0, len(artifact_ids), 200):
            batch = artifact_ids[start : start + 200]
            values = ", ".join(f"'{_escape_sql(value)}'" for value in batch)
            table.delete(f"artifact_id IN ({values})")
        table.add(records)
        from lancedb.index import FTS

        table.create_index(
            "text",
            config=FTS(
                base_tokenizer="ngram",
                ngram_min_length=2,
                ngram_max_length=3,
                stem=False,
                remove_stop_words=False,
            ),
            replace=True,
        )

    def exists(self) -> bool:
        import lancedb

        if not self.directory.exists():
            return False
        database = lancedb.connect(self.directory)
        return self.table_name in database.list_tables().tables

    def hybrid_search(
        self,
        query_text: str,
        query_vector: list[float],
        library_id: str,
        limit: int = 10,
        candidate_limit: int = 40,
    ) -> list[SearchHit]:
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector must have {self.dimensions} dimensions")
        if not self.exists():
            return []
        table = self._table(create=False)
        where = f"library_id = '{_escape_sql(library_id)}'"
        vector_rows = (
            table.search(query_vector, query_type="vector").where(where).limit(candidate_limit).to_list()
        )
        try:
            keyword_rows = (
                table.search(query_text, query_type="fts", fts_columns="text")
                .where(where)
                .limit(candidate_limit)
                .to_list()
            )
        except Exception:
            keyword_rows = []
        return _rrf(vector_rows, keyword_rows, limit)

    def _table(self, create: bool):
        import lancedb
        import pyarrow as pa

        self.directory.mkdir(parents=True, exist_ok=True)
        database = lancedb.connect(self.directory)
        if self.table_name in database.list_tables().tables:
            return database.open_table(self.table_name)
        if not create:
            raise ValueError("vector index is empty")
        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string(), nullable=False),
                pa.field("vector", pa.list_(pa.float32(), self.dimensions), nullable=False),
                pa.field("library_id", pa.string(), nullable=False),
                pa.field("item_id", pa.string(), nullable=False),
                pa.field("artifact_id", pa.string(), nullable=False),
                pa.field("attachment_id", pa.string(), nullable=False),
                pa.field("title", pa.string(), nullable=False),
                pa.field("year", pa.int32()),
                pa.field("text", pa.string(), nullable=False),
                pa.field("section", pa.string(), nullable=False),
                pa.field("page_start", pa.int32()),
                pa.field("page_end", pa.int32()),
                pa.field("content_hash", pa.string(), nullable=False),
                pa.field("embedding_provider", pa.string(), nullable=False),
                pa.field("embedding_model", pa.string(), nullable=False),
                pa.field("index_version", pa.string(), nullable=False),
            ]
        )
        return database.create_table(self.table_name, schema=schema)


def _rrf(vector_rows: list[dict], keyword_rows: list[dict], limit: int) -> list[SearchHit]:
    combined: dict[str, dict[str, Any]] = {}
    for source, rows in (("vector", vector_rows), ("keyword", keyword_rows)):
        for rank, row in enumerate(rows, 1):
            chunk_id = str(row["chunk_id"])
            entry = combined.setdefault(
                chunk_id,
                {"row": row, "score": 0.0, "vector_rank": None, "keyword_rank": None},
            )
            entry["score"] += 1.0 / (60 + rank)
            if row.get("attachment_id"):
                entry["score"] += 0.005
            entry[f"{source}_rank"] = rank
    ranked = sorted(combined.values(), key=lambda value: value["score"], reverse=True)[:limit]
    return [
        SearchHit(
            chunk_id=str(entry["row"]["chunk_id"]),
            item_id=str(entry["row"]["item_id"]),
            artifact_id=str(entry["row"]["artifact_id"]),
            title=str(entry["row"]["title"]),
            text=str(entry["row"]["text"]),
            section=str(entry["row"]["section"]),
            page_start=_optional_int(entry["row"].get("page_start")),
            page_end=_optional_int(entry["row"].get("page_end")),
            score=float(entry["score"]),
            vector_rank=entry["vector_rank"],
            keyword_rank=entry["keyword_rank"],
        )
        for entry in ranked
    ]


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
