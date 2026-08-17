from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from researchbrain.config import Settings
from researchbrain.db import models  # noqa: F401
from researchbrain.db.base import Base


def upgrade_schema(settings: Settings) -> None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    package_root = (
        Path(bundled_root) / "researchbrain" if bundled_root else Path(__file__).resolve().parents[1]
    )
    config = Config()
    config.set_main_option("script_location", str(package_root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    engine = create_engine(settings.database_url)
    table_names = set(inspect(engine).get_table_names())
    if table_names and "alembic_version" not in table_names:
        Base.metadata.create_all(engine, checkfirst=True)
        engine.dispose()
        command.stamp(config, "head")
        return
    engine.dispose()
    command.upgrade(config, "head")
