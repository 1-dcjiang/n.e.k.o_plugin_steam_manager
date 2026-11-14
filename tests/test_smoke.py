import tomllib
from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "steam_manager"' in text
    assert 'entry = "plugin.plugins.steam_manager:SteamManagerPlugin"' in text


def test_pack_rules_exclude_credentials() -> None:
    """实际打包排除配置必须在 pyproject.toml，而不只是文档说明。"""
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    rules = data["tool"]["neko"]["build"]["exclude_files"]
    assert "steam_api_key.txt" in rules
    assert "steam_cookie.txt" in rules
    assert data["project"]["version"] == "0.4.3"
