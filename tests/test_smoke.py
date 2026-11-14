import shutil
import tomllib
import zipfile
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
    assert data["project"]["version"] == "0.4.6"


def test_build_actually_excludes_credentials(tmp_path) -> None:
    """用真实 build 链路打包，凭证文件应被排除；只检查配置文本不算证据。"""
    from plugin.neko_plugin_cli.core import build_plugin

    src = tmp_path / "steam_manager"
    shutil.copytree(
        Path(__file__).resolve().parents[1],
        src,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".ruff_cache", ".pytest_cache", "dist", "build"),
    )
    (src / "steam_api_key.txt").write_text("DUMMY_SECRET_TEST", encoding="utf-8")
    (src / "steam_cookie.txt").write_text("DUMMY_COOKIE_TEST", encoding="utf-8")
    package = build_plugin(src, tmp_path / "steam_manager.neko-plugin").package_path
    names = zipfile.ZipFile(package).namelist()
    assert not [n for n in names if n.endswith(("steam_api_key.txt", "steam_cookie.txt"))]
