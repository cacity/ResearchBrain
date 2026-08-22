from researchbrain.config import UserConfigStore


def test_user_config_store_persists_only_allowed_values(tmp_path):
    store = UserConfigStore(tmp_path)
    result = store.update(
        {
            "contact_email": "test@example.org",
            "minimax_group_id": "group",
            "harness_port": 3081,
        }
    )

    assert result["contact_email"] == "test@example.org"
    assert store.load()["minimax_group_id"] == "group"
    assert store.load()["harness_port"] == 3081
    assert not list((tmp_path / "config").glob("*.tmp"))
