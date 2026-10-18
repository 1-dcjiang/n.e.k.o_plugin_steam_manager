"""steam_manager 单元测试：纯函数与错误分支（不依赖真实 Steam 安装）。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

# ══════════ parse_vdf ══════════


def test_parse_vdf_flat_key_values() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = '"root" { "name" "Counter-Strike 2" "appid" "730" }'
    data = parse_vdf(text)
    assert data["root"]["name"] == "Counter-Strike 2"
    assert data["root"]["appid"] == "730"


def test_parse_vdf_nested_blocks() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = (
        '"libraryfolders"\n'
        '{\n'
        '\t"1"\t"D:\\SteamLibrary"\n'
        '\t"2"\n'
        '\t{\n'
        '\t\t"path"\t"C:\\Games\\Steam"\n'
        '\t}\n'
        '}\n'
    )
    data = parse_vdf(text)
    assert data["libraryfolders"]["1"] == "D:\\SteamLibrary"
    assert data["libraryfolders"]["2"]["path"] == "C:\\Games\\Steam"


def test_parse_vdf_escaped_quotes() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = '"AppState" { "name" "a \\"quoted\\" name" }'
    data = parse_vdf(text)
    assert data["AppState"]["name"] == 'a "quoted" name'


def test_parse_vdf_empty() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    assert parse_vdf("") == {}
    assert parse_vdf(None) == {}


# ══════════ _state_flags_text ══════════


def test_state_flags_text() -> None:
    from plugin.plugins.steam_manager.steam import _state_flags_text

    assert _state_flags_text(4) == "已安装"
    assert _state_flags_text(2) == "需要更新"
    assert _state_flags_text(0) == "未安装"
    assert _state_flags_text(5) == "已安装"  # 4+1（含已安装位）


# ══════════ scan_games ══════════


def _make_fake_library(tmp_path, steamapps: dict[str, str]) -> str:
    """构造伪 Steam 目录树，返回 steam_path。"""
    apps_dir = tmp_path / "steamapps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    for app_id, name in steamapps.items():
        acf = (
            '"AppState"\n'
            '{\n'
            f'\t"appid"\t\t"{app_id}"\n'
            f'\t"name"\t\t"{name}"\n'
            '\t"StateFlags"\t"4"\n'
            '\t"SizeOnDisk"\t"10485760"\n'
            '}\n'
        )
        (apps_dir / f"appmanifest_{app_id}.acf").write_text(acf, encoding="utf-8")
    return str(tmp_path)


def test_scan_games_single_library(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Counter-Strike 2", "570": "DOTA 2"})
    games = scan_games(steam_path)
    assert len(games) == 2
    names = {g["name"] for g in games}
    assert names == {"Counter-Strike 2", "DOTA 2"}
    cs = next(g for g in games if g["app_id"] == "730")
    assert cs["installed"] is True
    assert cs["size_mb"] == 10.0


def test_scan_games_with_libraryfolders(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Main Game"})
    # 扩展库
    lib2 = tmp_path / "lib2"
    (lib2 / "steamapps").mkdir(parents=True)
    (lib2 / "steamapps" / "appmanifest_413150.acf").write_text(
        '"AppState"\n{\n\t"appid"\t"413150"\n\t"name"\t"Stardew Valley"\n\t"StateFlags"\t"4"\n}\n',
        encoding="utf-8",
    )
    vdf = (
        '"libraryfolders"\n'
        '{\n'
        f'\t"0"\t"{steam_path}"\n'
        f'\t"1"\t"{lib2}"\n'
        '}\n'
    )
    (tmp_path / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")
    games = scan_games(steam_path)
    assert len(games) == 2
    assert {g["name"] for g in games} == {"Main Game", "Stardew Valley"}


def test_scan_games_deduplicates(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Dup"})
    games = scan_games(steam_path)
    assert len(games) == 1


def test_find_game_by_appid_and_name(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import find_game

    steam_path = _make_fake_library(tmp_path, {"730": "Counter-Strike 2", "570": "DOTA 2"})
    assert find_game(steam_path, "730")[0]["name"] == "Counter-Strike 2"
    assert {g["name"] for g in find_game(steam_path, "counter")} == {"Counter-Strike 2"}
    assert len(find_game(steam_path, "")) == 2
    assert find_game(steam_path, "nonexistent-game-xyz") == []


def test_find_game_honors_limit(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import find_game

    steam_path = _make_fake_library(tmp_path, {"730": "Game A", "570": "Game B", "10": "Game C"})
    assert len(find_game(steam_path, "", limit=2)) == 2


# ══════════ launch_game 错误分支 ══════════


def test_launch_game_invalid_appid(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import launch_game

    result = launch_game(str(tmp_path), "abc")
    assert result["ok"] is False
    assert result["error"] == "INVALID_APP_ID"


def test_launch_game_exe_missing(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import launch_game

    result = launch_game(str(tmp_path), "730")
    assert result["ok"] is False
    assert result["error"] == "STEAM_EXE_NOT_FOUND"
