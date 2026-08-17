from pathlib import Path

import pytest

from researchbrain.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'data' / 'library.sqlite').as_posix()}",
        contact_email="test@example.org",
        worker_enabled=False,
    )
