from researchbrain.db.base import Database
from researchbrain.domain import CreatorInput, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository


def test_reference_is_deduplicated_by_doi(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    record = ReferenceRecord(
        title="A Test Paper",
        year=2026,
        identifiers={"doi": "10.1000/test"},
        creators=[CreatorInput(given="Ada", family="Lovelace")],
    )
    with database.session() as session:
        repository = LibraryRepository(session)
        library = repository.create_library("My Library", LibraryMode.STANDALONE)
        first, created_first = repository.add_reference(library.id, record, "fixture")
        second, created_second = repository.add_reference(library.id, record, "fixture")
        assert first.id == second.id
        assert created_first is True
        assert created_second is False
    database.engine.dispose()


def test_same_doi_can_exist_in_different_libraries(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    record = ReferenceRecord(title="Shared Paper", identifiers={"doi": "10.1000/shared"})
    with database.session() as session:
        repository = LibraryRepository(session)
        standalone = repository.create_library("Standalone", LibraryMode.STANDALONE)
        mirror = repository.create_library("Zotero", LibraryMode.ZOTERO_MIRROR)

        first, first_created = repository.add_reference(standalone.id, record, "fixture")
        second, second_created = repository.add_reference(mirror.id, record, "fixture")

        assert first.id != second.id
        assert first_created is True
        assert second_created is True
    database.engine.dispose()


def test_same_issn_can_be_shared_by_multiple_articles(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        repository = LibraryRepository(session)
        library = repository.create_library("Journal", LibraryMode.STANDALONE)
        first, first_created = repository.add_reference(
            library.id,
            ReferenceRecord(title="First article", identifiers={"issn": "1234-5678"}),
            "fixture",
        )
        second, second_created = repository.add_reference(
            library.id,
            ReferenceRecord(title="Second article", identifiers={"issn": "1234-5678"}),
            "fixture",
        )

        assert first.id != second.id
        assert first_created is True
        assert second_created is True
    database.engine.dispose()
