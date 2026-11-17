"""v0.4.13 增量深审回归：客户区截图、空标题拦截、DPI 返回值与发送歧义。"""

from __future__ import annotations

import asyncio
import http.client
import time

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _window() -> dict:
    return {
        "hwnd": 12345,
        "title": "Test Game",
        "pid": 999,
        "process": "game.exe",
        "rect": (100, 100, 500, 400),
        "client_rect": (108, 130, 508, 410),
        "ts": time.time(),
    }


def _plugin():
    from plugin.plugins.steam_manager import SteamManagerPlugin, control

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._game_window_title = "Test Game"
    inst._game_process = ""
    inst._require_friend_confirm = False
    inst._state = control.ControlState()
    inst._pending_confirms = {}
    inst._confirm_outbox = []
    inst.push_message = lambda **kwargs: (
        inst._confirm_outbox.append(kwargs) or {"ok": True, "submitted": True}
    )
    inst._civ6_ops_yield = lambda: None
    return inst, control


def test_capture_window_bmp_uses_client_rect_for_bitblt(monkeypatch) -> None:
    """截图源区域必须是客户区原点，不能再用完整窗口矩形。"""
    from plugin.plugins.steam_manager import control

    calls: dict[str, tuple] = {}

    class _User32:
        @staticmethod
        def GetClientRect(_hwnd, ptr):
            ptr._obj.left, ptr._obj.top = 0, 0
            ptr._obj.right, ptr._obj.bottom = 400, 280
            return 1

        @staticmethod
        def ClientToScreen(_hwnd, ptr):
            ptr._obj.x, ptr._obj.y = 108, 130
            return 1

        @staticmethod
        def GetDC(_hwnd):
            return 100

        @staticmethod
        def ReleaseDC(_hwnd, _hdc):
            return 1

    class _Gdi32:
        @staticmethod
        def CreateCompatibleDC(_hdc):
            return 200

        @staticmethod
        def CreateCompatibleBitmap(_hdc, width, height):
            calls["bitmap"] = (width, height)
            return 300

        @staticmethod
        def SelectObject(_hdc, _obj):
            return 0

        @staticmethod
        def BitBlt(_dst, _x, _y, width, height, _src, left, top, _rop):
            calls["bitblt"] = (width, height, left, top)
            return 1

        @staticmethod
        def GetDIBits(_hdc, _hbmp, _start, height, _buf, _bmi, _usage):
            return height

        @staticmethod
        def DeleteObject(_obj):
            return 1

        @staticmethod
        def DeleteDC(_hdc):
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "GDI32", _Gdi32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: None)
    monkeypatch.setattr(control, "_write_png", lambda *_args: "frame.png")

    path = control.capture_window_bmp({"hwnd": 12345, "rect": (100, 100, 500, 400)})
    assert path == "frame.png"
    assert calls["bitmap"] == (400, 280)
    assert calls["bitblt"] == (400, 280, 108, 130)


def test_client_capture_region_matches_click_conversion(monkeypatch) -> None:
    """同一客户区矩形同时驱动截图区域与点击换算。"""
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def GetClientRect(_hwnd, ptr):
            ptr._obj.left, ptr._obj.top = 0, 0
            ptr._obj.right, ptr._obj.bottom = 400, 280
            return 1

        @staticmethod
        def ClientToScreen(_hwnd, ptr):
            ptr._obj.x, ptr._obj.y = 108, 130
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: True)
    monkeypatch.setattr(control, "_screen_bounds", lambda: (0, 0, 2000, 1200))

    info = {"hwnd": 12345, "rect": (100, 100, 500, 400)}
    assert control._client_screen_rect(info) == (108, 130, 508, 410)
    assert control.window_client_to_screen(info, 1000, 1000) == (507, 409)


def test_dpi_zero_return_is_not_treated_as_success(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[str] = []

    class _User32:
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
    assert control._ensure_dpi_awareness() is False
    assert calls == ["context", "legacy"]


def test_dpi_legacy_fallback_is_treated_as_success(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def SetProcessDpiAwarenessContext(_value):
            return 0

        @staticmethod
        def SetProcessDPIAware():
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_DPI_AWARENESS_ATTEMPTED", False)
    monkeypatch.setattr(control, "_DPI_AWARE", False)
    assert control._ensure_dpi_awareness() is True


def test_game_capture_rejects_empty_title_and_no_process(monkeypatch) -> None:
    inst, control = _plugin()
    inst._game_window_title = ""
    inst._game_process = ""
    monkeypatch.setattr(
        control, "get_foreground_window_info",
        lambda: (_ for _ in ()).throw(AssertionError("不得读取任意前台窗口")),
    )
    result = _run(inst.game_capture())
    assert result["error"] == "WINDOW_TARGET_REQUIRED"


def test_watch_game_rejects_empty_title_and_no_process(monkeypatch) -> None:
    inst, control = _plugin()
    inst._game_window_title = ""
    inst._game_process = ""
    monkeypatch.setattr(
        control, "FrameWatcher",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得启动监控")),
    )
    result = _run(inst.watch_game(on=True, parameters={}))
    assert result["error"] == "WINDOW_TARGET_REQUIRED"
    assert inst._state.watch_enabled is False


def test_send_bad_status_line_is_uncertain(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    cookie = "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"
    monkeypatch.setattr(
        steam, "_open_cookie_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http.client.BadStatusLine("")),
    )
    with pytest.raises(steam.SteamSendUncertain, match="不确定"):
        steam.send_friend_message(cookie, "76561198000000001", "76561198000000002", "hi")


def test_send_read_connection_reset_is_uncertain(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    cookie = "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit=-1):
            raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr(steam, "_open_cookie_request", lambda *_args, **_kwargs: _Response())
    with pytest.raises(steam.SteamSendUncertain, match="不确定"):
        steam.send_friend_message(cookie, "76561198000000001", "76561198000000002", "hi")


def test_reply_maps_uncertain_send_to_distinct_error(monkeypatch) -> None:
    inst, _ = _plugin()
    from plugin.plugins.steam_manager import steam

    async def _sid(_steam_id=""):
        return "76561198000000000"

    inst._steam_id = _sid
    monkeypatch.setattr(steam, "api_key", lambda: "KEY")
    monkeypatch.setattr(steam, "community_cookie", lambda: "COOKIE")
    monkeypatch.setattr(steam, "friend_list", lambda *args: [
        {"steam_id": "76561198000000001", "name": "Alice"},
    ])
    monkeypatch.setattr(
        steam, "send_friend_message",
        lambda *_args: (_ for _ in ()).throw(steam.SteamSendUncertain("响应断开")),
    )
    result = _run(inst.steam_reply_friend(friend="Alice", text="hi"))
    assert result["error"] == "SEND_UNCERTAIN"
    assert "Steam 确认" in result["output"]


def test_summary_wraps_timeline_detail_once(monkeypatch) -> None:
    inst, control = _plugin()
    inst._state.note_event("action", "click (1,2) @ Test Game")
    result = _run(inst.game_state_summary())
    assert result["is_error"] is False
    assert result["output"].count("【不可信数据") == 1
