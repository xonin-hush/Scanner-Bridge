import json

import final


def test_missing_config_file_returns_defaults(tmp_path):
    config = final.load_config(tmp_path / "config.json")

    assert config == final.DEFAULT_CONFIG


def test_config_overrides_are_merged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"port": 9000, "scan_timeout_seconds": 60}))

    config = final.load_config(path)

    assert config["port"] == 9000
    assert config["scan_timeout_seconds"] == 60
    assert config["host"] == final.DEFAULT_CONFIG["host"]


def test_invalid_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")

    assert final.load_config(path) == final.DEFAULT_CONFIG


def test_non_object_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]")

    assert final.load_config(path) == final.DEFAULT_CONFIG


def test_clamp_dpi_bounds():
    assert final.clamp_dpi(99) == 100
    assert final.clamp_dpi(601) == 600
    assert final.clamp_dpi(200) == 200
    assert final.clamp_dpi("300") == 300
    assert final.clamp_dpi(None) == final.DEFAULT_CONFIG["default_dpi"]
    assert final.clamp_dpi("garbage") == final.DEFAULT_CONFIG["default_dpi"]
