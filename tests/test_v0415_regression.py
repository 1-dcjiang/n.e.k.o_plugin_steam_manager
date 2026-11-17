"""v0.4.15 增量深审（A707）回归：DPI 已感知判定、零尺寸客户区、纯进程名接管、发送歧义文案。

全部为 monkeypatch 离线用例：没有真实 Win32 窗口，也没有真实 Steam 响应，
只验证判定逻辑与拒绝分支，不代表实机已验证。
"""

from __future__ import annotations

import asyncio
import time

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _window(title: str = "Test Game", process: str = "game.exe", hwnd: int = 12345) -> dict:
    return {
        "hwnd": hwnd,
        "title": title,
        "pid": 999,
        "process": process,
        "rect": (100, 100, 500, 400),
        "client_rect": (108, 130, 508, 410),
        "ts": time.time(),
    }


def _plugin():
    from plugin.plugins.steam_manager import SteamManagerPlugin, control

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._game_window_title = ""
    inst._game_process = "game.exe"
    inst._require_friend_confirm = False
    inst._state = control.ControlState()
    inst._pending_confirms = {}
    inst._confirm_outbox = []
    inst.push_message = lambda **kwargs: {"ok": True, "submitted": True}
    inst._civ6_ops_yield = lambda: None
    inst._consume_confirm = lambda *args, **kwargs: True
    return inst, control


# ══════════ 1. DPI：已感知不应被当成失败 ══════════


def test_dpi_already_aware_is_success_without_set_calls(monkeypatch, caplog) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[str] = []

    class _User32:
        @staticmethod
        def GetThreadDpiAwarenessContext():
            return 3

        @staticmethod
        def GetAwarenessFromDpiAwarenessContext(_ctx):
            return 2  # PER_MONITOR_AWARE

        @staticmethod
        def SetProcessDpiAwarenessContext(_value):
            calls.append("context")
            return 0

        @staticmethod
        def SetProcessDPIAware():
            calls.append("legacy")
            return 0

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)
    with caplog.at_level("WARNING"):
        assert control._ensure_dpi_awareness() is True
    assert calls == []  # 已感知就不必再调两个 Set，避免返回 0 引发误导性警告
    assert "没有可用的 DPI awareness API" not in caplog.text


def test_dpi_awareness_invalid_value_falls_back_to_set(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[str] = []

    class _User32:
        @staticmethod
        def GetThreadDpiAwarenessContext():
            return 0

        @staticmethod
        def GetAwarenessFromDpiAwarenessContext(_ctx):
            return -1  # DPI_AWARENESS_INVALID

        @staticmethod
        def SetProcessDpiAwarenessContext(_value):
            calls.append("context")
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)
    assert control._ensure_dpi_awareness() is True
    assert calls == ["context"]


def test_dpi_legacy_query_when_thread_api_missing(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[str] = []

    class _User32:
        @staticmethod
        def IsProcessDPIAware():
            return 1

        @staticmethod
        def SetProcessDPIAware():
            calls.append("legacy")
            return 0

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)
    assert control._ensure_dpi_awareness() is True
    assert calls == []


def test_dpi_unaware_and_all_sets_zero_still_reports_failure(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def GetThreadDpiAwarenessContext():
            return 0

        @staticmethod
        def GetAwarenessFromDpiAwarenessContext(_ctx):
            return 0  # DPI_AWARENESS_UNAWARE

        @staticmethod
        def SetProcessDpiAwarenessContext(_value):
            return 0

        @staticmethod
        def SetProcessDPIAware():
            return 0

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)
    assert control._ensure_dpi_awareness() is False


# ══════════ 2. 零尺寸客户区必须判失败 ══════════


class _ZeroClientUser32:
    @staticmethod
    def GetClientRect(_hwnd, ptr):
        ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 0, 0, 0, 0
        return 1

    @staticmethod
    def ClientToScreen(_hwnd, ptr):
        ptr._obj.x, ptr._obj.y = 108, 130
        return 1


def test_client_screen_rect_zero_size_returns_none(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _ZeroClientUser32())
    assert control._client_screen_rect({"hwnd": 1}) is None


def test_capture_rejects_zero_size_client_instead_of_1x1(monkeypatch, tmp_path) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _ZeroClientUser32())
    monkeypatch.setattr(control, "GDI32", object())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    assert control.capture_window_bmp(_window(), save_dir=str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []  # 不能留下 1×1 的“成功截图”


def test_click_conversion_rejects_zero_size_client(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _ZeroClientUser32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    with pytest.raises(ValueError, match="客户区"):
        control.window_client_to_screen({"hwnd": 1}, 500, 500)


# ══════════ 3. 纯进程名查找与接管 ══════════


class _ProcessOnlyUser32:
    """两个可见窗口：Steam 客户端（steam.exe）与游戏（game.exe）。"""

    titles = {11: "Steam", 22: "Test Game"}
    pids = {11: 111, 22: 999}

    @staticmethod
    def EnumWindows(cb, _lparam):
        for hwnd in (11, 22):
            cb(hwnd, 0)
        return 1

    @staticmethod
    def IsWindowVisible(_hwnd):
        return 1

    @staticmethod
    def GetWindowTextW(hwnd, buf, _len):
        text = _ProcessOnlyUser32.titles.get(hwnd, "")
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
        ptr._obj.value = _ProcessOnlyUser32.pids.get(hwnd, 0)
        return 1

    @staticmethod
    def GetClientRect(_hwnd, ptr):
        ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 0, 0, 400, 280
        return 1

    @staticmethod
    def ClientToScreen(_hwnd, ptr):
        ptr._obj.x, ptr._obj.y = 108, 130
        return 1


def _fake_process_name(pid: int) -> str:
    return {111: "steam.exe", 999: "game.exe"}.get(pid, "")


def test_find_window_by_process_only_matches_game_window(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _ProcessOnlyUser32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_process_name", _fake_process_name)
    found = control.find_window_by_title("", "game.exe")
    assert found is not None
    assert found["hwnd"] == 22
    assert found["process"] == "game.exe"


def test_find_window_by_process_only_skips_other_processes(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", _ProcessOnlyUser32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_process_name", _fake_process_name)
    assert control.find_window_by_title("", "nope.exe") is None


def test_find_window_without_title_and_process_refuses_enumeration(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def EnumWindows(*_args):
            raise AssertionError("标题与进程名都为空时不得枚举任意窗口")

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    assert control.find_window_by_title("", "") is None


def test_take_control_accepts_process_only_target(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    seen: list[str] = []

    async def _fake_find(hint):
        seen.append(hint)
        return target

    monkeypatch.setattr(inst, "_find_game_window", _fake_find)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: dict(target))
    inst._state.note_frame(target, "frame.png")
    result = _run(inst.take_control(on=True, grant_code="123456"))
    assert result["is_error"] is False, result
    assert seen == [""]
    assert "接管已开启" in result["output"]


def test_take_control_process_only_without_window_still_refuses(monkeypatch) -> None:
    inst, control = _plugin()

    async def _fake_find(_hint):
        return None

    monkeypatch.setattr(inst, "_find_game_window", _fake_find)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: _window("Browser", "chrome.exe"))
    result = _run(inst.take_control(on=True, grant_code="123456"))
    assert result["error"] == "NO_WINDOW"
    assert inst._state.control_enabled is False


def test_take_control_requires_title_or_process(monkeypatch) -> None:
    inst, _control = _plugin()
    inst._game_window_title = ""
    inst._game_process = ""
    result = _run(inst.take_control(on=True, grant_code="123456"))
    assert result["error"] == "WINDOW_TARGET_REQUIRED"


# ══════════ 4. 连接阶段断链的文案不再断言“已到达” ══════════


def test_send_connect_stage_failure_wording_is_honest(monkeypatch) -> None:
    import urllib.error

    from plugin.plugins.steam_manager import steam

    cookie = "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"
    monkeypatch.setattr(
        steam, "_open_cookie_request",
        lambda *_a, **_k: (_ for _ in ()).throw(
            urllib.error.URLError(TimeoutError("timed out"))
        ),
    )
    with pytest.raises(steam.SteamSendUncertain) as excinfo:
        steam.send_friend_message(cookie, "76561198000000001", "76561198000000002", "hi")
    message = str(excinfo.value)
    assert "不确定" in message
    assert "无法确认" in message
    assert "不要直接重发" in message
