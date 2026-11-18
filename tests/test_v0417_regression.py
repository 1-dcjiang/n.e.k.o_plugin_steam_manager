"""v0.4.17 增量深审（A794）回归：空标题授权、纯进程名选窗、DPI 标志、GDI 清理。

全部为 monkeypatch 离线用例，不代表真实 Steam / 游戏窗口已验证。
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import time


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _plugin(title: str = "", process: str = "game.exe"):
    from plugin.plugins.steam_manager import SteamManagerPlugin, control

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._game_window_title = title
    inst._game_process = process
    inst._require_friend_confirm = False
    inst._state = control.ControlState()
    inst._pending_confirms = {}
    inst._confirm_outbox = []
    inst.push_message = lambda **kwargs: {"ok": True, "submitted": True}
    inst._civ6_ops_yield = lambda: None
    inst._consume_confirm = lambda *args, **kwargs: True
    return inst, control


def test_take_control_process_only_uses_process_label(monkeypatch) -> None:
    inst, _ = _plugin(title="", process="game.exe")
    issued: list[dict] = []
    pushed: list[str] = []

    def _issue(kind, **payload):
        issued.append({"kind": kind, **payload})
        return "tok", "123456"

    monkeypatch.setattr(inst, "_issue_confirm", _issue)
    monkeypatch.setattr(
        inst, "_push_confirm_code", lambda title, detail, code: pushed.append(title) or True
    )
    result = _run(inst.take_control(on=True, grant_code=""))
    assert result["error"] == "USER_CONFIRM_REQUIRED"
    assert issued and issued[0]["game"] == "game.exe"
    assert pushed and "game.exe" in pushed[0]


def test_take_control_empty_target_rejects_before_issuing(monkeypatch) -> None:
    inst, _ = _plugin(title="", process="")
    calls: list[dict] = []
    monkeypatch.setattr(inst, "_issue_confirm", lambda kind, **kw: calls.append(kw) or ("t", "1"))
    result = _run(inst.take_control(on=True, grant_code=""))
    assert result["error"] == "WINDOW_TARGET_REQUIRED"
    assert calls == []  # 不再先推送“允许接管「」”的空目标授权码


def test_take_control_process_only_binds_grant_to_process(monkeypatch) -> None:
    inst, control = _plugin(title="", process="game.exe")
    target = {
        "hwnd": 12345,
        "title": "Test Game",
        "pid": 999,
        "process": "game.exe",
        "rect": (100, 100, 500, 400),
        "client_rect": (108, 130, 508, 410),
        "ts": time.time(),
    }
    seen: list[tuple[bool, str]] = []

    def _consume(code, kind, *, consume=True, **expected):
        seen.append((consume, expected.get("game", "")))
        return True

    async def _find(_hint):
        return target

    monkeypatch.setattr(inst, "_consume_confirm", _consume)
    monkeypatch.setattr(inst, "_find_game_window", _find)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: dict(target))
    inst._state.note_frame(target, "frame.png")
    result = _run(inst.take_control(on=True, grant_code="123456"))
    assert result["is_error"] is False
    assert seen == [(False, "game.exe"), (True, "game.exe")]


def test_startup_process_only_clears_default_title(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    inst, _ = _plugin(title="civilization vi", process="")

    class _Config:
        async def dump(self, timeout):
            return {"steam": {"game_process": "Factorio.exe"}}

    inst.config = _Config()
    monkeypatch.setattr(steam, "find_steam_path", lambda: None)
    result = _run(inst.on_startup())
    assert result["status"] == "ready"
    assert inst._game_window_title == ""
    assert inst._game_process == "Factorio.exe"


class _MultiWindowUser32:
    """同进程 22 是前台主窗但面积较小；55 是更大的设置窗，必须让位给前台。"""

    titles = {11: "Steam", 22: "Test Game", 33: "", 44: "Helper", 55: "Game Settings"}
    pids = {11: 111, 22: 999, 33: 999, 44: 999, 55: 999}
    areas = {22: (400, 280), 55: (800, 600)}

    @staticmethod
    def EnumWindows(cb, _lparam):
        for hwnd in (33, 44, 11, 22, 55):
            cb(hwnd, 0)
        return 1

    @staticmethod
    def GetForegroundWindow():
        return 22

    @staticmethod
    def IsWindowVisible(_hwnd):
        return 1

    @staticmethod
    def GetWindowTextW(hwnd, buf, _len):
        text = _MultiWindowUser32.titles.get(hwnd, "")
        buf.value = text
        return len(text)

    @staticmethod
    def GetWindowLongW(_hwnd, _idx):
        return 0

    @staticmethod
    def GetWindowRect(_hwnd, ptr):
        ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 100, 100, 500, 400
        return 1

    @staticmethod
    def GetWindowThreadProcessId(hwnd, ptr):
        ptr._obj.value = _MultiWindowUser32.pids.get(hwnd, 0)
        return 1

    @staticmethod
    def GetClientRect(hwnd, ptr):
        if hwnd == 44:
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 0, 0, 0, 0
        else:
            width, height = _MultiWindowUser32.areas.get(hwnd, (400, 280))
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 0, 0, width, height
        return 1

    @staticmethod
    def ClientToScreen(_hwnd, ptr):
        ptr._obj.x, ptr._obj.y = 108, 130
        return 1


def _fake_process_name(pid: int) -> str:
    return {111: "steam.exe", 999: "game.exe"}.get(pid, "")


def test_process_only_prefers_foreground_over_larger_window(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _MultiWindowUser32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_process_name", _fake_process_name)
    found = control.find_window_by_title("", "game.exe")
    assert found is not None
    assert found["hwnd"] == 22


def test_process_only_falls_back_to_largest_client_area(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _NoForeground(_MultiWindowUser32):
        @staticmethod
        def GetForegroundWindow():
            return 0

    monkeypatch.setattr(control, "USER32", _NoForeground())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_process_name", _fake_process_name)
    found = control.find_window_by_title("", "game.exe")
    assert found is not None
    assert found["hwnd"] == 55


def test_dpi_system_aware_warns_about_mixed_dpi(monkeypatch, caplog) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def GetThreadDpiAwarenessContext():
            return 3

        @staticmethod
        def GetAwarenessFromDpiAwarenessContext(_ctx):
            return 1  # SYSTEM_AWARE

    monkeypatch.setattr(control, "USER32", _User32())
    with caplog.at_level(logging.WARNING):
        assert control._is_dpi_aware() is True
    assert "SYSTEM_AWARE" in caplog.text


def test_dpi_attempted_flag_is_set_after_work(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def SetProcessDpiAwarenessContext(_value):
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)

    def _already_aware():
        assert control._DPI_AWARENESS_ATTEMPTED is False
        return True

    monkeypatch.setattr(control, "_is_dpi_aware", _already_aware)
    assert control._ensure_dpi_awareness() is True
    assert control._DPI_AWARENESS_ATTEMPTED is True


def test_capture_bmp_memory_error_still_releases_gdi(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[str] = []

    class _User32:
        @staticmethod
        def GetDC(_hwnd):
            return 100

        @staticmethod
        def ReleaseDC(_hwnd, _hdc):
            calls.append("release")
            return 1

    class _Gdi32:
        @staticmethod
        def CreateCompatibleDC(_hdc):
            return 200

        @staticmethod
        def CreateCompatibleBitmap(_hdc, _w, _h):
            return 300

        @staticmethod
        def SelectObject(_hdc, _obj):
            return 0

        @staticmethod
        def BitBlt(*_args):
            return 1

        @staticmethod
        def DeleteObject(_obj):
            calls.append("delete-object")
            return 1

        @staticmethod
        def DeleteDC(_hdc):
            calls.append("delete-dc")
            return 1

    def _raise_memory_error(*_args, **_kwargs):
        raise MemoryError

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "GDI32", _Gdi32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_client_screen_rect", lambda _info: (0, 0, 10, 10))
    monkeypatch.setattr(ctypes, "create_string_buffer", _raise_memory_error)
    assert control.capture_window_bmp({"hwnd": 1}) is None
    assert calls == ["delete-object", "delete-dc", "release"]


def test_civ6_ops_yield_ignores_empty_localappdata(monkeypatch) -> None:
    inst, _ = _plugin()
    monkeypatch.setenv("LOCALAPPDATA", "")

    def _boom(*_args, **_kwargs):
        raise AssertionError("LOCALAPPDATA 为空时不得去当前目录找运营锁")

    monkeypatch.setattr("os.path.isfile", _boom)
    assert inst._civ6_ops_yield() is None
