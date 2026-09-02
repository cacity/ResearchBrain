from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, replace

from researchbrain.agent.service import Evidence
from researchbrain.discovery.service import DiscoveryRecord
from researchbrain.orchestration.models import EvidenceRelevanceJudgment
from researchbrain.retrieval.index import SearchHit


@dataclass(frozen=True)
class LedgerEntry:
    fingerprint: str
    evidence: Evidence
    level: str
    query: str


class EvidenceLedger:
    def __init__(self, local_prefix: str = "L", max_chunks_per_item: int = 3):
        self.local_prefix = local_prefix
        self.max_chunks_per_item = max_chunks_per_item
        self._local: dict[str, tuple[SearchHit, str]] = {}
        self._online: dict[str, tuple[DiscoveryRecord, str]] = {}
        self._screening: dict[str, EvidenceRelevanceJudgment] = {}

    def add_local(self, query: str, hits: list[SearchHit]) -> int:
        added = 0
        for hit in hits:
            if hit.chunk_id in self._local:
                continue
            self._local[hit.chunk_id] = (hit, query)
            added += 1
        return added

    def add_online(self, query: str, records: list[DiscoveryRecord]) -> int:
        added = 0
        for record in records:
            key = _online_key(record)
            existing = self._online.get(key)
            if existing and len(existing[0].abstract) >= len(record.abstract):
                continue
            self._online[key] = (record, query)
            added += int(existing is None)
        return added

    def entries(self, limit: int = 40, *, include_excluded: bool = False) -> list[LedgerEntry]:
        local_by_item: dict[str, list[tuple[SearchHit, str]]] = defaultdict(list)
        for hit, query in self._local.values():
            local_by_item[hit.item_id].append((hit, query))

        selected_local: list[tuple[SearchHit, str]] = []
        for hits in local_by_item.values():
            selected_local.extend(
                sorted(hits, key=lambda value: value[0].score, reverse=True)[: self.max_chunks_per_item]
            )
        selected_local.sort(key=lambda value: value[0].score, reverse=True)

        entries: list[LedgerEntry] = []
        for index, (hit, query) in enumerate(selected_local[:limit], 1):
            level = local_evidence_level(hit)
            evidence = Evidence(
                id=f"{self.local_prefix}{index}",
                chunk_id=hit.chunk_id,
                item_id=hit.item_id,
                title=hit.title,
                text=hit.text,
                section=hit.section,
                page_start=hit.page_start,
                page_end=hit.page_end,
                score=hit.score,
            )
            entries.append(LedgerEntry(hit.chunk_id, evidence, level, query))

        remaining = max(0, limit - len(entries))
        online_records = sorted(
            self._online.items(),
            key=lambda value: (bool(value[1][0].abstract), value[1][0].year or 0),
            reverse=True,
        )[:remaining]
        for index, (fingerprint, (record, query)) in enumerate(online_records, 1):
            evidence = _online_evidence(index, record)
            entries.append(
                LedgerEntry(
                    fingerprint,
                    evidence,
                    "structured_abstract" if record.abstract else "metadata",
                    query,
                )
            )
        screened: list[LedgerEntry] = []
        for entry in entries:
            judgment = self._screening.get(entry.fingerprint)
            if judgment:
                entry = LedgerEntry(
                    entry.fingerprint,
                    replace(
                        entry.evidence,
                        relevance=judgment.relevance,
                        relevance_reason=judgment.reason,
                    ),
                    entry.level,
                    entry.query,
                )
            if include_excluded or not judgment or judgment.relevance == "relevant":
                screened.append(entry)
        return screened

    def evidence(self, limit: int = 40, *, include_excluded: bool = False) -> list[Evidence]:
        return [
            entry.evidence
            for entry in self.entries(limit, include_excluded=include_excluded)
        ]

    def summary(self, limit: int = 40, *, include_excluded: bool = False) -> list[dict]:
        return [
            {
                "id": entry.evidence.id,
                "title": entry.evidence.title,
                "level": entry.level,
                "section": entry.evidence.section,
                "page_start": entry.evidence.page_start,
                "source_kind": entry.evidence.source_kind,
                "query": entry.query,
                "excerpt": entry.evidence.text[:700],
                "screening": (
                    self._screening[entry.fingerprint].model_dump()
                    if entry.fingerprint in self._screening
                    else None
                ),
            }
            for entry in self.entries(limit, include_excluded=include_excluded)
        ]

    def apply_screening(self, judgments: list[EvidenceRelevanceJudgment]) -> None:
        by_id = {
            entry.evidence.id: entry.fingerprint
            for entry in self.entries(include_excluded=True)
        }
        for judgment in judgments:
            fingerprint = by_id.get(judgment.evidence_id)
            if fingerprint:
                self._screening[fingerprint] = judgment

    def screening_counts(self) -> dict[str, int]:
        counts = {"relevant": 0, "adjacent": 0, "irrelevant": 0, "unreviewed": 0}
        for entry in self.entries(include_excluded=True):
            judgment = self._screening.get(entry.fingerprint)
            counts[judgment.relevance if judgment else "unreviewed"] += 1
        return counts

    def evidence_ids_for_subquestion(self, subquestion_id: str) -> list[str]:
        values: list[str] = []
        for entry in self.entries():
            judgment = self._screening.get(entry.fingerprint)
            if judgment and subquestion_id in judgment.subquestion_ids:
                values.append(entry.evidence.id)
        return values


def local_evidence_level(hit: SearchHit) -> str:
    if hit.chunk_id.startswith("metadata:") or hit.section == "题录与摘要":
        return "structured_abstract" if "Abstract:" in hit.text else "metadata"
    if hit.page_start is not None:
        return "fulltext_page"
    return "fulltext_section"


def _online_key(record: DiscoveryRecord) -> str:
    identifiers = record.identifiers or {}
    canonical = record.doi or identifiers.get("doi") or identifiers.get("pmid") or identifiers.get("arxiv")
    if canonical:
        return f"online:{canonical.lower()}"
    normalized = " ".join(record.title.lower().split())
    digest = hashlib.sha256(f"{normalized}\n{record.abstract}".encode()).hexdigest()[:24]
    return f"online:{digest}"


def _online_evidence(index: int, record: DiscoveryRecord) -> Evidence:
    identifiers = record.identifiers or {}
    identity = "; ".join(f"{key.upper()}: {value}" for key, value in identifiers.items())
    fields = [f"Title: {record.title}"]
    if record.authors:
        fields.append(f"Authors: {', '.join(record.authors)}")
    if record.venue:
        fields.append(f"Venue: {record.venue}")
    if record.year:
        fields.append(f"Year: {record.year}")
    if record.doi and "doi" not in identifiers:
        fields.append(f"DOI: {record.doi}")
    if identity:
        fields.append(identity)
    if record.abstract:
        fields.append(f"Abstract: {record.abstract}")
    return Evidence(
        id=f"W{index}",
        chunk_id=f"web:{record.source}:{record.source_id}",
        item_id="",
        title=record.title,
        text="\n".join(fields),
        section="online title/abstract",
        page_start=None,
        page_end=None,
        score=1.0,
        source_kind="online",
        source_name=", ".join(record.sources or [record.source]),
        source_url=record.url,
        discovery_record=asdict(record),
    )
