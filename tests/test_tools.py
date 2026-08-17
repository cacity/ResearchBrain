from researchbrain.db.base import Database
from researchbrain.domain import CreatorInput, LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository
from researchbrain.tools import ResearchBrainTools


def test_tools_list_get_and_export(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    with database.session() as session:
        library = LibraryRepository(session).create_library("Tools", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(
                title="Tool Paper",
                identifiers={"doi": "10.1000/tool"},
                creators=[CreatorInput(given="Ada", family="Lovelace")],
            ),
            "fixture",
        )
        item_id = item.id
    database.engine.dispose()

    tools = ResearchBrainTools(settings)
    assert tools.list_libraries()[0]["name"] == "Tools"
    assert tools.get_item(item_id)["identifiers"]["doi"] == "10.1000/tool"
    assert tools.export_references([item_id], "doi")["content"] == "10.1000/tool\n"
    tools.close()
