import json

import pytest

from researchbrain.citations.export import CitationExporter
from researchbrain.db.base import Database
from researchbrain.domain import CreatorInput, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository


def test_csl_markdown_and_doi_exports(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="Exported Paper",
                year=2026,
                container_title="Journal of Tests",
                identifiers={"doi": "10.1000/export"},
                creators=[CreatorInput(given="Ada", family="Lovelace")],
            ),
            "fixture",
        )
        exporter = CitationExporter(session)
        csl = json.loads(exporter.export([item.id], "csl-json").content)
        markdown = exporter.export([item.id], "markdown").content
        dois = exporter.export([item.id], "doi").content
        assert csl[0]["DOI"] == "10.1000/export"
        assert csl[0]["author"][0]["family"] == "Lovelace"
        assert "Journal of Tests" in markdown
        assert dois == "10.1000/export\n"
    database.engine.dispose()


def test_bibtex_and_ris_exports_when_components_are_installed(settings):
    pytest.importorskip("bibtexparser")
    pytest.importorskip("rispy")
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Formats", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="Serializable Reference",
                year=2026,
                identifiers={"doi": "10.1000/formats"},
                creators=[CreatorInput(given="Ada", family="Lovelace")],
            ),
            "fixture",
        )
        exporter = CitationExporter(session)
        bibtex = exporter.export([item.id], "bibtex").content
        ris = exporter.export([item.id], "ris").content

        assert "@article" in bibtex
        assert "doi = {10.1000/formats}" in bibtex
        assert "TY  - JOUR" in ris
        assert "DO  - 10.1000/formats" in ris
    database.engine.dispose()
