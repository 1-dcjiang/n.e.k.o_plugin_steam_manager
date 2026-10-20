"""steam_manager v0.2.1 测试：入口注册/分派 + 纯函数（不触发真实 Steam 操作）。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入。
"""

from __future__ import annotations

import asyncio


def _plugin():
    from plugin.plugins.steam_manager import SteamManagerPlugin

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._state = None
    return inst


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_entry_registered() -> None:
    """plugin_entry run 入口可被宿主收集（入口点 > 0，v0.2.1 修复）。"""
    from plugin.plugins.steam_manager import SteamManagerPlugin

    metas = []
    for name in dir(SteamManagerPlugin):
        if name.startswith("_"):
            continue
        meta = getattr(getattr(SteamManagerPlugin, name, None), "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            metas.append(meta)
    assert any(m.id == "run" for m in metas), f"缺少入口 run，现有：{[m.id for m in metas]}"


def test_entry_run_unknown_action() -> None:
    inst = _plugin()
    result = _run(inst.entry_run(action="fly", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_ACTION"


def test_entry_run_launch_requires_game() -> None:
    """action=launch 但未给 game → NO_GAME（不触发真实启动）。"""
    inst = _plugin()
    result = _run(inst.entry_run(action="launch", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_GAME"


def test_entry_run_default_is_status(monkeypatch) -> None:
    """无 action 时默认走 status；Steam 不可探测时安全返回 STEAM_NOT_FOUND。"""
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "find_steam_path", lambda: None)
    inst = _plugin()
    result = _run(inst.entry_run(parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "STEAM_NOT_FOUND"


def test_entry_run_control_off_is_safe() -> None:
    """action=control on=false → 交还（在无状态对象上应安全返回）。"""
    inst = _plugin()

    class _FakeState:
        control_enabled = False
        last_window = None

        def disable_control(self):
            return None

        def enable_control(self):
            return False

        def recent_window_active(self, within_seconds=15):
            return False

        def note_event(self, *a, **k):
            return None

        def note_frame(self, *a, **k):
            return None

        def summary(self):
            return {"watch_enabled": False, "control_enabled": False}

    inst._state = _FakeState()
    result = _run(inst.entry_run(action="control", on=False, parameters={}))
    assert result["is_error"] is False  # 交还总是安全成功
    assert "交还" in result["output"]


def test_parse_vk() -> None:
    """按键解析：单字母 → ASCII；多位数字 → VK 码；非法 → None。"""
    from plugin.plugins.steam_manager import _parse_vk

    assert _parse_vk("a") == ord("A")
    assert _parse_vk("5") == ord("5")
    assert _parse_vk("27") == 27  # ESC
    assert _parse_vk("") is None
    assert _parse_vk("abc") is None
