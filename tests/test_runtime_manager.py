import hashlib
import zipfile

import pytest

from researchbrain.runtime.manager import RuntimeInstallError, RuntimeManager


def test_runtime_manager_installs_and_activates_verified_archive(tmp_path):
    archive = tmp_path / "mineru.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("bin/mineru.exe", b"fixture")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manager = RuntimeManager(tmp_path / "data")
    state = manager.install_archive("mineru", "3.0.0", archive, digest)
    assert (tmp_path / "data" / "runtime" / "mineru" / "3.0.0" / "bin" / "mineru.exe").exists()
    assert manager.status()["components"]["mineru"]["version"] == "3.0.0"
    assert state.sha256 == digest


def test_runtime_manager_rejects_zip_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside.exe", b"unsafe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(RuntimeInstallError, match="unsafe path"):
        RuntimeManager(tmp_path / "data").install_archive("mineru", "3.0.0", archive, digest)
    assert not (tmp_path / "data" / "outside.exe").exists()
