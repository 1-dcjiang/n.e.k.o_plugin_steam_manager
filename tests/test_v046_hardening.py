"""v0.4.6 深审修复回归测试：A1-A11 / B1-B2 的硬边界。

覆盖：接管必须绑定前台游戏窗口、确认码带外推送、civ6 运营锁下可停车、
SendInput 失败不报成功、好友全量后过滤、无价格不显示免费、HTTP 总预算、
cookie 身份核对、消息体 sessionid、单实例帧监控。
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.error

import pytest


def _plugin():
    from plugin.plugins.steam_manager import SteamManagerPlugin, control

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._game_window_title = "Test Game"
    inst._state = control.ControlState()
    inst._pending_confirms = {}
    inst._confirm_outbox = []

    def _push_message(**kwargs):
        inst._confirm_outbox.append(kwargs)
        return {"ok": True}

    inst.push_message = _push_message
    inst._civ6_ops_yield = lambda: None
    return inst, control


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _window(title: str = "Test Game", process: str = "game.exe", hwnd: int = 12345) -> dict:
    return {"hwnd": hwnd, "title": title, "pid": 999, "process": process,
            "rect": (100, 100, 500, 400), "ts": time.time()}


def _code(inst) -> str:
    text = inst._confirm_outbox[-1]["parts"][0]["text"]
    match = re.search(r"确认码：(\d{6})", text)
    assert match, text
    return match.group(1)


# ══════════ A1 / A2：接管绑定前台游戏窗口 ══════════


def test_take_control_pushes_code_out_of_band(monkeypatch) -> None:
    inst, control = _plugin()
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: _window())

    result = _run(inst.take_control(on=True, parameters={}))
    assert result["error"] == "USER_CONFIRM_REQUIRED"
    assert result.get("confirm_delivery") == "user_ui"
    assert "confirm_token" not in result and "grant_code" not in result
    assert _code(inst)
    pushed = inst._confirm_outbox[-1]
    assert pushed["visibility"] == ["chat"]
    assert pushed["ai_behavior"] == "blind"
    assert pushed["parts"][0]["type"] == "text"


def test_confirm_code_three_wrong_attempts_invalidate(monkeypatch) -> None:
    inst, control = _plugin()
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: _window())
    _run(inst.take_control(on=True, parameters={}))
    correct = _code(inst)
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(3):
        result = _run(inst.take_control(on=True, grant_code=wrong, parameters={}))
        assert result["error"] == "GRANT_CODE_INVALID"
    # 三次错误后该条确认已作废，正确码也不能再用
    result = _run(inst.take_control(on=True, grant_code=correct, parameters={}))
    assert result["error"] == "GRANT_CODE_INVALID"


def test_take_control_rejects_target_not_foreground(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: target)
    monkeypatch.setattr(control, "get_foreground_window_info",
                        lambda: _window("Chrome", "chrome.exe", 777))
    monkeypatch.setattr(inst, "_consume_confirm", lambda *a, **k: True)

    result = _run(inst.take_control(on=True, grant_code="123456", parameters={}))
    assert result["error"] == "TARGET_NOT_FOREGROUND"
    assert inst._state.control_enabled is False


def test_take_control_rejects_browser_process_even_with_matching_title(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window("Test Game", "chrome.exe")
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: target)
    monkeypatch.setattr(inst, "_consume_confirm", lambda *a, **k: True)

    result = _run(inst.take_control(on=True, grant_code="123456", parameters={}))
    assert result["error"] == "TARGET_NOT_ALLOWED"


def test_take_control_binds_target_and_enables(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, r"C:\tmp\frame.bmp")
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: target)
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(inst, "_consume_confirm", lambda *a, **k: True)

    result = _run(inst.take_control(on=True, grant_code="123456", parameters={}))
    assert result["is_error"] is False
    assert inst._state.control_enabled is True
    assert inst._state.control_target["hwnd"] == target["hwnd"]


# ══════════ A5：运营锁期间允许停车 ══════════


def test_take_control_off_works_while_civ6_lock(monkeypatch) -> None:
    inst, _control = _plugin()
    inst._civ6_ops_yield = lambda: {"output": "civ6 正在操作，稍后", "is_error": True, "error": "CIV6_BUSY"}

    result = _run(inst.take_control(on=False, parameters={}))
    assert result["is_error"] is False
    assert inst._state.control_enabled is False


# ══════════ A6：SendInput 失败不报“已输入” ══════════


def test_game_action_input_failure_is_reported(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, r"C:\tmp\frame.bmp")
    assert inst._state.enable_control(target) is True
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(control, "type_text", lambda text: max(0, len(text) - 1))

    result = _run(inst.game_action(action="text", text="abc", parameters={}))
    assert result["error"] == "INPUT_FAILED"
    assert "已输入" not in result["output"]


def test_game_action_click_reports_input_failed(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, r"C:\tmp\frame.bmp")
    assert inst._state.enable_control(target) is True
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(control, "mouse_click_abs", lambda x, y: False)

    result = _run(inst.game_action(action="click", x=10, y=20, parameters={}))
    assert result["error"] == "INPUT_FAILED"
    assert "已点击" not in result["output"]


def test_game_action_key_reports_input_failed(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, r"C:\tmp\frame.bmp")
    assert inst._state.enable_control(target) is True
    monkeypatch.setattr(control, "get_foreground_window_info", lambda: target)
    monkeypatch.setattr(control, "press_key", lambda vk: False)

    result = _run(inst.game_action(action="key", key="a", parameters={}))
    assert result["error"] == "INPUT_FAILED"
    assert "已按键" not in result["output"]


def test_game_action_aborts_when_target_leaves_foreground(monkeypatch) -> None:
    inst, control = _plugin()
    target = _window()
    inst._state.note_frame(target, r"C:\tmp\frame.bmp")
    assert inst._state.enable_control(target) is True
    monkeypatch.setattr(control, "get_foreground_window_info",
                        lambda: _window("Chrome", "chrome.exe", 777))
    clicked: list[tuple[int, int]] = []
    monkeypatch.setattr(control, "mouse_click_abs", lambda x, y: clicked.append((x, y)) or True)

    result = _run(inst.game_action(action="click", x=10, y=20, parameters={}))
    assert result["error"] == "TARGET_NOT_FOREGROUND"
    assert clicked == []
    assert inst._state.control_enabled is False


def test_friend_summaries_respects_deadline(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    def _no_http(*_a, **_k):
        raise AssertionError("预算已耗尽，不应再发请求")

    monkeypatch.setattr(steam, "_http_get_json", _no_http)
    errors: list[str] = []
    info = steam._summaries_in_chunks(
        "KEY", ["1", "2", "3"], errors=errors, deadline=time.monotonic() - 1
    )
    assert info == {}
    assert "预算用尽" in errors[0]
    assert "3 位好友" in errors[0]


# ══════════ A7 / A8：好友全量过滤 / 无价格不显示免费 ══════════


def test_friends_filter_runs_after_full_list(monkeypatch) -> None:
    inst, _control = _plugin()
    from plugin.plugins.steam_manager import steam

    called: list[int] = []

    def _friends(_key, _sid, limit, _errors):
        called.append(limit)
        return [
            {"steam_id": "76561198000000001", "name": "Alice", "state": "在线"},
            {"steam_id": "76561198000000002", "name": "Bob", "state": "离线"},
            {"steam_id": "76561198000000003", "name": "LastFriend", "state": "在线"},
        ]

    monkeypatch.setattr(steam, "friend_list", _friends)
    monkeypatch.setattr(steam, "api_key", lambda: "KEY")

    async def _sid(_steam_id=""):
        return "76561198000000000"

    inst._steam_id = _sid
    result = _run(inst.steam_friends(name_filter="last", parameters={}))
    assert called == [0]
    assert result["is_error"] is False
    assert "LastFriend" in result["output"]
    assert "不可信数据" in result["output"]


def test_friends_filter_does_not_claim_no_match_when_summaries_missing(monkeypatch) -> None:
    inst, _control = _plugin()
    from plugin.plugins.steam_manager import steam

    def _friends(_key, _sid, _limit, errors):
        errors.append("好友概要总时间预算用尽：剩余 1 位好友未获取昵称")
        return [
            {"steam_id": "1", "name": "Alice", "state": "在线", "summary_ok": True},
            {"steam_id": "2", "name": "", "state": "离线", "summary_ok": False},
        ]

    monkeypatch.setattr(steam, "friend_list", _friends)
    monkeypatch.setattr(steam, "api_key", lambda: "KEY")

    async def _sid(_steam_id=""):
        return "76561198000000000"

    inst._steam_id = _sid
    result = _run(inst.steam_friends(name_filter="Bob", parameters={}))
    assert result["error"] == "MATCH_INCOMPLETE"
    assert result["partial"] is True
    assert result["unresolved"] == 1
    assert "无法确认" in result["output"]
    assert "已检查全部" not in result["output"]
    assert "预算用尽" in result["output"]


def test_game_price_unknown_is_not_free(monkeypatch) -> None:
    inst, _control = _plugin()
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "app_price", lambda _appid: {
        "app_id": "123", "name": "Unpublished", "price_cny": None,
        "original_cny": None, "discount": 0, "free": False, "price_known": False,
    })
    result = _run(inst.steam_game_price(game="123", parameters={}))
    assert result["is_error"] is False
    assert "无价格数据" in result["output"]
    assert "免费" not in result["output"]


def test_app_price_only_trusts_is_free(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_http_get_json", lambda *a, **k: {
        "123": {"success": True, "data": {"name": "NoPrice", "price_overview": {}}}
    })
    info = steam.app_price("123")
    assert info["free"] is False
    assert info["price_known"] is False


def test_app_price_recognizes_free_game(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_http_get_json", lambda *a, **k: {
        "570": {"success": True, "data": {"name": "Dota 2", "is_free": True}}
    })
    info = steam.app_price("570")
    assert info["free"] is True
    assert info["price_known"] is True


# ══════════ A11：HTTP 总预算 ══════════


def test_http_retry_respects_total_budget(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    def _boom(*_a, **_k):
        raise urllib.error.HTTPError("https://x", 500, "boom", {}, None)

    monkeypatch.setattr(steam.urllib.request, "urlopen", _boom)
    start = time.monotonic()
    with pytest.raises(steam.SteamHTTPError):
        steam._http_get_json("https://example.com/x", total_budget=0.3, retries=3)
    assert time.monotonic() - start < 1.0


def test_http_read_respects_total_budget(monkeypatch, patch_module_clock) -> None:
    from plugin.plugins.steam_manager import steam

    class _Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = _Clock()

    class _Socket:
        def __init__(self):
            self.timeouts: list[float] = []

        def settimeout(self, value):
            self.timeouts.append(value)

    socket = _Socket()

    class _Raw:
        _sock = socket

    class _Fp:
        raw = _Raw()

    class _Resp:
        fp = _Fp()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _size=-1):
            clock.now += 0.2  # 首个读取块返回时，0.1 秒总预算已经耗尽
            return b'{"ok": true}'

    patch_module_clock(monkeypatch, steam, monotonic=clock)
    monkeypatch.setattr(steam.urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    with pytest.raises(steam.SteamHTTPError) as caught:
        steam._http_get_json("https://example.com/x", total_budget=0.1, retries=0)
    assert caught.value.status == 408
    assert socket.timeouts and socket.timeouts[0] <= 0.1


# ══════════ B1 / B2：Cookie 身份与 sessionid ══════════


def test_cookie_identity_parsed_from_steam_login(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    cookie = "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"
    assert steam._cookie_steam_id(cookie) == "76561198000000001"


def test_send_message_includes_sessionid_and_checks_sender(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    captured: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"[1, true]"

    def _urlopen(req, timeout=None):
        captured.append({"body": req.data.decode(), "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(steam.urllib.request, "urlopen", _urlopen)
    cookie = "sessionid=abc; steamLoginSecure=76561198000000001%7C%7Ctoken"
    result = steam.send_friend_message(cookie, "76561198000000001", "76561198000000002", "hi")
    assert result["ok"] is True
    body = captured[0]["body"]
    assert "sessionid=abc" in body
    assert "steamid_first=76561198000000002" in body


def test_send_message_rejects_cookie_of_another_account(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    cookie = "sessionid=abc; steamLoginSecure=76561198000000009%7C%7Ctoken"
    with pytest.raises(ValueError, match="另一个账号"):
        steam.send_friend_message(cookie, "76561198000000001", "76561198000000002", "hi")


# ══════════ A9：同进程只允许一个监控线程 ══════════


def test_frame_watcher_is_singleton(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control.FrameWatcher, "_loop",
                        lambda self: self._stop.wait(0.15))
    state = control.ControlState()
    first = control.FrameWatcher(state, interval=1)
    second = control.FrameWatcher(state, interval=1)
    try:
        first.start()
        assert control.FrameWatcher._ACTIVE is first
        second.start()
        assert control.FrameWatcher._ACTIVE is second
    finally:
        second.stop()
        first.stop()
        control.FrameWatcher._ACTIVE = None


# ══════════ 轻量：ASCII 数字与 VK 白名单 ══════════


def test_fullwidth_digits_rejected() -> None:
    from plugin.plugins.steam_manager import _is_ascii_digits, _parse_vk

    assert _is_ascii_digits("１２３") is False
    assert _is_ascii_digits("123") is True
    assert _parse_vk("999") is None
    assert _parse_vk("F5") == 0x74
    assert _parse_vk("space") == 0x20