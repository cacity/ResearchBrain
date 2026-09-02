from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema


def test_upgrade_schema_sets_revision(settings):
    settings.ensure_directories()
    upgrade_schema(settings)
    database = Database(settings.database_url)
    assert database.current_schema_revision() == "20260902_0007"
    database.engine.dispose()
