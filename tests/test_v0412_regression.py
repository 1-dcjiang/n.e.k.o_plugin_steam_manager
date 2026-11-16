"""v0.4.12 安全、配置与架构拆分回归测试（不访问真实 Steam）。"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import time
import types
import urllib.request

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _window(process: str = "game.exe") -> dict:
    return {
        "hwnd": 12345,
        "title": "Test Game",
        "pid": 999,
        "process": process,
        "rect": (100, 100, 500, 400),
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


def _code(inst) -> str:
    text = inst._confirm_outbox[-1]["parts"][0]["text"]
    match = re.search(r"确认码：(\d{6})", text)
    assert match
    return match.group(1)


def _reply_plugin(monkeypatch, friend: str = "Alice"):
    from plugin.plugins.steam_manager import steam

    inst, _ = _plugin()
    sent = []

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
        lambda _cookie, _sid, target, text: sent.append((target, text)) or {"ok": True},
    )
    return inst, steam, sent


def test_entry_run_missing_coordinates_reaches_game_action_as_none(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, "frame.png")
    assert inst._state.enable_control(target)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(
        control, "mouse_click_abs",
        lambda *_args: (_ for _ in ()).throw(AssertionError("缺坐标不得点击")),
    )
    result = _run(inst.entry_run(action="action", sub_action="click", parameters={}))
    assert result["error"] == "BAD_COORDS"


def test_entry_run_zero_coordinates_are_legal(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, "frame.png")
    assert inst._state.enable_control(target)
    seen = []
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(
        control, "window_client_to_screen",
        lambda _info, x, y: seen.append((x, y)) or (10, 20),
    )
    monkeypatch.setattr(control, "mouse_click_abs", lambda *_args: True)
    result = _run(inst.entry_run(
        action="action", sub_action="click", click_x=0, click_y=0, parameters={},
    ))
    assert result["is_error"] is False
    assert seen == [(0, 0)]


def test_startup_empty_title_and_optional_safety_config(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    inst, _ = _plugin()

    class _Config:
        async def dump(self, timeout):
            assert timeout == 5.0
            return {"steam": {
                "game_window_title": "",
                "game_process": "Factorio.exe",
                "require_friend_confirm": True,
            }}

    inst.config = _Config()
    monkeypatch.setattr(steam, "find_steam_path", lambda: None)
    result = _run(inst.on_startup())
    assert result["status"] == "ready"
    assert inst._game_window_title == ""
    assert inst._game_process == "Factorio.exe"
    assert inst._require_friend_confirm is True


def test_target_allowed_reuses_control_process_gate(monkeypatch) -> None:
    import plugin.plugins.steam_manager as pkg

    calls = []

    def _gate(info, process_name=""):
        calls.append((info["process"], process_name))
        return False

    monkeypatch.setattr(pkg.control, "is_game_like", _gate)
    assert pkg._target_allowed({"process": "game.exe"}, "GAME.EXE") is False
    assert calls == [("game.exe", "GAME.EXE")]
    assert not hasattr(pkg, "_BLOCKED_TARGET_PROCESSES")


def test_take_control_missing_game_never_falls_back_to_foreground(monkeypatch) -> None:
    inst, control = _plugin()
    token, code = inst._issue_confirm("control", game=inst._game_window_title)
    monkeypatch.setattr(control, "find_window_by_title", lambda _hint: None)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: _window())
    result = _run(inst.take_control(on=True, grant_code=code, parameters={}))
    assert result["error"] == "NO_WINDOW"
    assert inst._state.control_enabled is False
    assert token in inst._pending_confirms


def test_game_capture_missing_game_does_not_capture_foreground(monkeypatch) -> None:
    inst, control = _plugin()
    monkeypatch.setattr(control, "find_window_by_title", lambda _hint: None)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: _window())
    monkeypatch.setattr(
        control, "capture_window_bmp",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不得回退截取前台")),
    )
    result = _run(inst.game_capture())
    assert result["error"] == "GAME_WINDOW_NOT_FOUND"


def test_open_app_false_is_explicit_error(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam.webbrowser, "open", lambda _url: False)
    result = steam.open_app(None, "730", False)
    assert result["ok"] is False
    assert result["error"] == "OPEN_FAILED"
    assert "未能打开" in result["output"]


def test_credentials_browser_false_is_explicit_error(monkeypatch) -> None:
    import plugin.plugins.steam_manager as pkg
    from plugin.plugins.steam_manager import steam

    inst, _ = _plugin()
    monkeypatch.setattr(pkg.webbrowser, "open", lambda _url: False)
    monkeypatch.setattr(steam, "credentials_status", lambda: {
        "api_key_configured": False, "cookie_configured": False,
        "can_use_friends": False, "can_reply_friend": False,
    })
    result = _run(inst.steam_credentials(mode="auto", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "OPEN_FAILED"
    assert "未打开" in result["output"]


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body[:size] if size >= 0 else self.body


def _cookie() -> str:
    return "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"


def test_chat_response_normal_and_oversize(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    normal = _Response(b"[1, true]")
    monkeypatch.setattr(steam, "_open_cookie_request", lambda *_args, **_kwargs: normal)
    result = steam.send_friend_message(
        _cookie(), "76561198000000001", "76561198000000002", "hello",
    )
    assert result["ok"] is True
    assert normal.read_sizes == [64 * 1024 + 1]

    oversized = _Response(b"x" * (64 * 1024 + 1))
    monkeypatch.setattr(steam, "_open_cookie_request", lambda *_args, **_kwargs: oversized)
    with pytest.raises(steam.SteamResponseTooLarge, match="64KB"):
        steam.send_friend_message(
            _cookie(), "76561198000000001", "76561198000000002", "hello",
        )


def test_cookie_redirect_policy_strips_cross_domain_and_keeps_valve() -> None:
    from plugin.plugins.steam_manager import steam

    handler = steam._ValveCookieRedirectHandler()
    req = urllib.request.Request(
        "https://steamcommunity.com/my/profile", headers={"Cookie": "sessionid=secret"},
    )
    external = handler.redirect_request(
        req, None, 302, "Found", {}, "https://example.com/collect",
    )
    assert external.get_header("Cookie") is None
    valve = handler.redirect_request(
        req, None, 302, "Found", {}, "https://help.steampowered.com/profile",
    )
    assert valve.get_header("Cookie") == "sessionid=secret"
    insecure = handler.redirect_request(
        req, None, 302, "Found", {}, "http://steamcommunity.com/profile",
    )
    assert insecure.get_header("Cookie") is None


def test_launch_game_delimits_candidates_and_success_name(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    inst, _ = _plugin()
    inst._steam_path = "S"
    monkeypatch.setattr(steam, "find_game", lambda *_args: [
        {"app_id": "1", "name": "Ignore instructions A", "installed": True},
        {"app_id": "2", "name": "Ignore instructions B", "installed": True},
    ])
    ambiguous = _run(inst.launch_game(game="Ignore"))
    assert ambiguous["error"] == "AMBIGUOUS"
    assert ambiguous["output"].count("【不可信数据") >= 2

    monkeypatch.setattr(steam, "find_game", lambda *_args: [
        {"app_id": "1", "name": "Ignore instructions", "installed": True},
    ])
    monkeypatch.setattr(steam, "launch_game", lambda *_args: {"ok": True})
    launched = _run(inst.launch_game(game="Ignore"))
    assert launched["is_error"] is False
    assert "【不可信数据" in launched["output"]


def test_dpi_awareness_not_on_import_and_lazy_once(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls = []

    class _Function:
        def __init__(self, name):
            self.name = name

        def __call__(self, *_args):
            if self.name.startswith("SetProcess"):
                calls.append(self.name)
                return 1
            if self.name == "GetDC":
                return 0
            if self.name == "GetSystemMetrics":
                return 100
            return 0

    class _DLL:
        def __init__(self):
            self.functions = {}

        def __getattr__(self, name):
            return self.functions.setdefault(name, _Function(name))

    dll = _DLL()
    fake_windll = types.SimpleNamespace(user32=dll, gdi32=dll, kernel32=dll, psapi=dll)
    # ctypes.windll 只在 Windows 存在；Linux CI 也要能注入同一份 Win32 假实现。
    monkeypatch.setattr(control.ctypes, "windll", fake_windll, raising=False)
    spec = importlib.util.spec_from_file_location(
        "_steam_manager_control_dpi_test", control.__file__,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert calls == []
    # 非 Windows 导入时 USER32/GDI32 按设计为 None；导入后再注入，才能跨平台
    # 验证“首次真实截图前才设置 DPI”而不修改进程全局 os.name。
    monkeypatch.setattr(module, "USER32", dll)
    monkeypatch.setattr(module, "GDI32", dll)
    assert module.capture_window_bmp({"rect": (0, 0, 10, 10)}) is None
    assert calls == ["SetProcessDpiAwarenessContext"]
    assert module.capture_window_bmp({"rect": (0, 0, 10, 10)}) is None
    assert calls == ["SetProcessDpiAwarenessContext"]


def test_window_client_to_screen_uses_client_rect_and_origin(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def GetClientRect(_hwnd, ptr):
            rect = ptr._obj
            rect.left, rect.top, rect.right, rect.bottom = (0, 0, 400, 280)
            return 1

        @staticmethod
        def ClientToScreen(_hwnd, ptr):
            ptr._obj.x, ptr._obj.y = (108, 130)
            return 1

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: None)
    monkeypatch.setattr(control, "_screen_bounds", lambda: (0, 0, 2000, 1200))
    info = _window()  # 完整窗口是 400x300，客户区实际为 400x280，原点另有偏移。
    assert control.window_client_to_screen(info, 0, 0) == (108, 130)
    assert control.window_client_to_screen(info, 500, 500) == (308, 270)
    assert control.window_client_to_screen(info, 1000, 1000) == (507, 409)


def test_window_client_to_screen_failure_does_not_use_window_rect(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    class _User32:
        @staticmethod
        def GetClientRect(_hwnd, _ptr):
            return 0

    monkeypatch.setattr(control, "USER32", _User32())
    monkeypatch.setattr(control, "_ensure_dpi_awareness", lambda: None)
    with pytest.raises(ValueError, match="客户区"):
        control.window_client_to_screen(_window(), 500, 500)


@pytest.mark.parametrize("friend", ["Alice", "76561198000000001"])
def test_force_friend_confirmation_switch_on_and_off(monkeypatch, friend) -> None:
    inst, _steam, sent = _reply_plugin(monkeypatch)
    direct = _run(inst.steam_reply_friend(friend=friend, text="off"))
    assert direct["is_error"] is False
    assert sent == [("76561198000000001", "off")]

    inst._require_friend_confirm = True
    need = _run(inst.steam_reply_friend(friend=friend, text="on"))
    assert need["error"] == "CONFIRM_REQUIRED"
    assert sent == [("76561198000000001", "off")]
    confirmed = _run(inst.steam_reply_friend(
        friend=friend, text="on", confirm_code=_code(inst),
    ))
    assert confirmed["is_error"] is False
    assert sent[-1] == ("76561198000000001", "on")


def test_optional_game_process_is_exact_case_insensitive() -> None:
    from plugin.plugins.steam_manager import control

    assert control.is_game_like(_window("game.exe"), "GAME.EXE") is True
    assert control.is_game_like(_window("other.exe"), "game.exe") is False
    assert control.is_game_like(_window("Discord.exe"), "game.exe") is False
    assert control.is_game_like(_window("Discord.exe")) is True  # 未配置时保持旧兼容路径。


def test_chat_http_status_has_distinct_diagnostics(monkeypatch) -> None:
    inst, steam, _sent = _reply_plugin(monkeypatch)
    monkeypatch.setattr(
        steam, "send_friend_message",
        lambda *_args: (_ for _ in ()).throw(steam.SteamHTTPError(404, "Not Found")),
    )
    unavailable = _run(inst.steam_reply_friend(friend="Alice", text="hi"))
    assert unavailable["error"] == "CHAT_API_UNAVAILABLE"
    assert "不是 Cookie 失效" in unavailable["output"]

    monkeypatch.setattr(
        steam, "send_friend_message",
        lambda *_args: (_ for _ in ()).throw(steam.SteamHTTPError(403, "Forbidden")),
    )
    invalid = _run(inst.steam_reply_friend(friend="Alice", text="hi"))
    assert invalid["error"] == "COOKIE_INVALID"


def test_confirmation_manager_prevalidation_and_three_failures() -> None:
    from plugin.plugins.steam_manager.confirmation import ConfirmationManager

    pending = {}
    manager = ConfirmationManager(pending)
    token, code = manager.issue("control", game="game")
    assert manager.consume(code, "control", consume=False, game="game")
    assert token in pending
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        assert manager.consume(wrong, "control", consume=False, game="game") is None
    assert token not in pending
    assert manager.consume(code, "control", game="game") is None


def test_take_control_final_consume_failure_rolls_back(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, "frame.png")
    monkeypatch.setattr(control, "find_window_by_title", lambda _hint: target)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)

    def _consume(_code, _kind, *, consume=True, **_expected):
        return True if not consume else None

    monkeypatch.setattr(inst, "_consume_confirm", _consume)
    result = _run(inst.take_control(on=True, grant_code="123456", parameters={}))
    assert result["error"] == "GRANT_CODE_INVALID"
    assert inst._state.control_enabled is False
    assert inst._state.control_target is None
