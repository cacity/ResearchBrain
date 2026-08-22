import json

import pytest

from researchbrain.harness.runtime import (
    HarnessInstallError,
    HarnessRuntimeManager,
    _checksum_for,
    _node_supported,
    _validate_port,
)


def test_node_version_floor_and_port_validation():
    assert not _node_supported("20.20.2")
    assert not _node_supported("22.18.9")
    assert _node_supported("22.19.0")
    assert _node_supported("24.1.0")
    assert _validate_port(3080) == 3080
    with pytest.raises(HarnessInstallError):
        _validate_port(80)


def test_checksum_parser_requires_exact_filename():
    checksums = "abc123  node-v24.1.0-win-x64.zip\ndef456  node-v24.1.0-linux-x64.tar.xz\n"
    assert _checksum_for(checksums, "node-v24.1.0-win-x64.zip") == "abc123"
    with pytest.raises(HarnessInstallError):
        _checksum_for(checksums, "node-v24.1.0-win-arm64.zip")


def test_manager_writes_isolated_bundle_and_skill(tmp_path):
    manager = HarnessRuntimeManager(tmp_path)

    manager._write_integration_files()

    manifest = json.loads((manager.bundle / "package.json").read_text(encoding="utf-8"))
    skill = manager.workspace / ".agents" / "skills" / "researchbrain-literature" / "SKILL.md"
    patch = (manager.bundle / "cordis.patch.yml").read_text(encoding="utf-8")
    assert manifest["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert skill.is_file()
    assert "get_research_context" in skill.read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-mcp-client" in patch
    assert "RESEARCHBRAIN_DATA_DIR" in patch
    assert not manager.status()["configured"]
