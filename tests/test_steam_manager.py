"""steam_manager v0.2.1 测试：入口注册/分派 + 纯函数（不触发真实 Steam 操作）。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入。
"""

from __future__ import annotations

import asyncio


def _plugin():
    from plugin.plugins.steam_manager import SteamManagerPlugin, control

    inst = SteamManagerPlugin.__new__(SteamManagerPlugin)
    inst._steam_path = None
    inst._watcher = None
    inst._state = control.ControlState()
    inst._pending_confirms = {}
    inst._confirm_outbox = []

    def _push_message(**kwargs):
        inst._confirm_outbox.append(kwargs)
        return {"ok": True, "submitted": True}

    inst.push_message = _push_message
    return inst


def _last_code(inst) -> str:
    text = inst._confirm_outbox[-1]["parts"][0]["text"]
    return text.split("确认码：", 1)[1][:6]


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

# ══════════ v0.3.2：凭证自动配置（工具层） ══════════


def test_credentials_skip_mode_rejects_passed_credentials() -> None:
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="skip", api_key="A", parameters={}))
    assert result["error"] == "MODE_CONFLICT"


def test_credentials_unknown_mode_rejected() -> None:
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="whatever", parameters={}))
    assert result["error"] == "INVALID_MODE"


def test_credentials_skip_mode() -> None:
    """skip：不配置，返回状态说明且不写文件。"""
    from plugin.plugins.steam_manager import steam

    orig_api = steam.api_key
    orig_cookie = steam.community_cookie
    steam.api_key = lambda: ""
    steam.community_cookie = lambda: ""
    try:
        inst = _plugin()
        result = _run(inst.steam_credentials(mode="skip", parameters={}))
        assert result["is_error"] is False
        assert "不配置" in result["output"]
        assert result["result"]["mode"] == "skip"
    finally:
        steam.api_key = orig_api
        steam.community_cookie = orig_cookie


def test_credentials_yui_writes_and_verifies(monkeypatch, tmp_path) -> None:
    """yui：提供 API Key → 加密写入并验证；验证失败如实报错。"""
    from plugin.plugins.steam_manager import steam

    written: list[str] = []
    monkeypatch.setattr(steam, "save_api_key", lambda key, path=None: written.append(key) or str(tmp_path / "k.txt"))
    monkeypatch.setattr(steam, "validate_api_key", lambda key: (False, "InvalidKey，请检查是否复制完整"))
    monkeypatch.setattr(steam, "credentials_status", lambda: {"api_key_configured": True, "cookie_configured": False, "can_use_friends": True, "can_reply_friend": False})
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="yui", api_key="BADKEY", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "CREDENTIAL_INVALID"
    assert "验证未通过" in result["output"]
    assert written == []


def test_credentials_auto_without_credential_opens_guide(monkeypatch) -> None:
    """auto 且无凭证：打开官方获取页并给手动粘贴指引，绝不探测浏览器 Cookie。"""
    import plugin.plugins.steam_manager as pkg
    from plugin.plugins.steam_manager import steam

    opened: list[str] = []
    monkeypatch.setattr(pkg, "webbrowser", type("W", (), {"open": staticmethod(lambda u: opened.append(u) or True)})())
    monkeypatch.setattr(steam, "credentials_status", lambda: {"api_key_configured": False, "cookie_configured": False, "can_use_friends": False, "can_reply_friend": False})
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="auto", parameters={}))
    assert result["is_error"] is False
    assert len(opened) == 1
    assert "steamcommunity.com/dev/apikey" in opened[0]
    assert "API Key" in result["output"]
    assert "不会自动探测浏览器 Cookie" in result["output"]
    assert result["result"]["source"] == "manual"
    assert not hasattr(steam, "probe_browser_cookies")


def test_entry_run_setup_routes(monkeypatch) -> None:
    """入口 action=setup 路由到 steam_credentials。"""
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "credentials_status", lambda: {"api_key_configured": False, "cookie_configured": False, "can_use_friends": False, "can_reply_friend": False})
    inst = _plugin()
    result = _run(inst.entry_run(action="setup", mode="skip", parameters={}))
    assert result["is_error"] is False
    assert "不配置" in result["output"]


def test_game_capture_uses_configured_window_title(monkeypatch, tmp_path) -> None:
    """game_capture 使用配置的窗口标题，不再硬编码 Civilization VI。"""
    import time

    import plugin.plugins.steam_manager as pkg
    from plugin.plugins.steam_manager import control

    inst = _plugin()
    inst._game_window_title = "factorio"
    inst._state = control.ControlState()
    inst._civ6_ops_yield = lambda: None
    hints: list[str] = []

    monkeypatch.setattr(control, "get_foreground_window_info", lambda: {
        "title": "Windows Terminal", "process": "wt.exe", "pid": 1,
        "rect": (0, 0, 100, 100), "ts": time.time(),
    })
    monkeypatch.setattr(control, "find_window_by_title", lambda hint: hints.append(hint) or {
        "title": "Factorio 1.1", "process": "factorio.exe", "pid": 2,
        "rect": (0, 0, 200, 100), "ts": time.time(),
    })
    frame = tmp_path / "frame.bmp"
    frame.write_bytes(b"BM")
    monkeypatch.setattr(control, "capture_window_printwindow", lambda info: str(frame))
    monkeypatch.setattr(pkg, "_to_png", lambda p: p)
    monkeypatch.setattr(pkg, "_image_entry", lambda p: [{"data_b64": "eA==", "mime": "image/png"}])

    result = _run(inst.game_capture())
    assert result["is_error"] is False
    assert hints == ["factorio"]
    assert "Factorio 1.1" in result["output"]
    assert result["images"]


# ══════════ v0.4.3：好友精确匹配 / 凭证先验后写 / 全库扫描 ══════════


def _reply_plugin(monkeypatch):
    from plugin.plugins.steam_manager import steam

    inst = _plugin()

    async def _sid(_steam_id=""):
        return "MY-STEAM-ID"

    inst._steam_id = _sid
    monkeypatch.setattr(steam, "api_key", lambda: "KEY")
    monkeypatch.setattr(steam, "community_cookie", lambda: "COOKIE")
    return inst, steam


def test_reply_friend_numeric_nickname_exact_match(monkeypatch) -> None:
    """纯数字昵称只要精确同名，就按昵称发，不再默认当 SteamID。"""
    inst, steam = _reply_plugin(monkeypatch)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(steam, "friend_list", lambda *a: [
        {"steam_id": "76561198000000001", "name": "12345"},
        {"steam_id": "76561198000000002", "name": "someone"},
    ])
    monkeypatch.setattr(steam, "send_friend_message", lambda _c, _s, target, text: sent.append((target, text)) or {"ok": True})

    result = _run(inst.steam_reply_friend(friend="12345", text="hi"))
    assert result["is_error"] is False
    assert sent == [("76561198000000001", "hi")]


def test_reply_friend_numeric_non_id_rejected_without_substring(monkeypatch) -> None:
    """纯数字既不等于好友 SteamID、也无同名好友时拒绝，绝不按子串猜人。"""
    inst, steam = _reply_plugin(monkeypatch)
    monkeypatch.setattr(steam, "friend_list", lambda *a: [
        {"steam_id": "76561198000000001", "name": "12345abc"},
        {"steam_id": "76561198000000002", "name": "76561198000000003"},
    ])
    monkeypatch.setattr(steam, "send_friend_message", lambda *a: (_ for _ in ()).throw(AssertionError("不应发送")))

    result = _run(inst.steam_reply_friend(friend="12345", text="hi"))
    assert result["is_error"] is True
    assert result["error"] == "NO_FRIEND"
    assert "不按子串匹配" in result["output"]


def test_reply_friend_exact_steam_id_and_ambiguous_nickname(monkeypatch) -> None:
    inst, steam = _reply_plugin(monkeypatch)
    friends = [
        {"steam_id": "76561198000000001", "name": "Same"},
        {"steam_id": "76561198000000002", "name": "Same"},
        {"steam_id": "76561198000000003", "name": "Other"},
    ]
    monkeypatch.setattr(steam, "friend_list", lambda *a: friends)
    monkeypatch.setattr(steam, "send_friend_message", lambda *a: {"ok": True})

    ambiguous = _run(inst.steam_reply_friend(friend="Same", text="hi"))
    assert ambiguous["error"] == "AMBIGUOUS"
    by_id = _run(inst.steam_reply_friend(friend="76561198000000003", text="hi"))
    assert by_id["is_error"] is False


def test_credentials_cookie_invalid_not_saved(monkeypatch, tmp_path) -> None:
    from plugin.plugins.steam_manager import steam

    cookie_writes: list[str] = []
    monkeypatch.setattr(steam, "validate_community_cookie", lambda c: (False, "缺少 sessionid"))
    monkeypatch.setattr(steam, "save_community_cookie", lambda c, path=None: cookie_writes.append(c) or str(tmp_path))
    monkeypatch.setattr(steam, "credentials_status", lambda: {"api_key_configured": False, "cookie_configured": False, "can_use_friends": False, "can_reply_friend": False})
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="yui", cookie="BAD", parameters={}))
    assert result["is_error"] is True
    assert "验证未通过" in result["output"]
    assert cookie_writes == []


def test_open_app_scans_full_library(monkeypatch) -> None:
    """按 AppID 打开时必须全量扫描，不能只取前 200 个游戏。"""
    from plugin.plugins.steam_manager import steam

    limits: list[int] = []
    launched: list[str] = []

    def _find_game(_path, _query, limit=100):
        limits.append(limit)
        return [{"app_id": "999", "name": "Big Library Game", "installed": True}]

    async def _launch_game(self, *, game="", **_):
        launched.append(game)
        return {"output": "已启动", "is_error": False}

    monkeypatch.setattr(steam, "find_game", _find_game)
    inst = _plugin()
    inst._steam_path = "S"
    inst.launch_game = _launch_game.__get__(inst, type(inst))
    result = _run(inst.steam_open_app(game="999"))
    assert result["is_error"] is False
    assert limits == [0]
    assert launched == ["999"]


def test_reply_friend_substring_requires_confirm(monkeypatch) -> None:
    """非数字昵称只有子串匹配时，先返回确认，不直接发送。"""
    inst, steam = _reply_plugin(monkeypatch)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(steam, "friend_list", lambda *a: [
        {"steam_id": "76561198000000001", "name": "Alice Smith"},
    ])
    monkeypatch.setattr(steam, "send_friend_message", lambda _c, _s, target, text: sent.append((target, text)) or {"ok": True})

    need = _run(inst.steam_reply_friend(friend="Alice", text="hi"))
    assert need["error"] == "CONFIRM_REQUIRED"
    assert "confirm_token" not in need
    code = _last_code(inst)
    assert sent == []

    ok = _run(inst.steam_reply_friend(friend="Alice", text="hi", confirm_code=code))
    assert ok["is_error"] is False
    assert sent == [("76561198000000001", "hi")]


def test_reply_friend_no_key_steam_id_requires_confirm(monkeypatch) -> None:
    """没有 API Key 时，17 位 SteamID 也必须二次确认，不能盲发。"""
    inst, steam = _reply_plugin(monkeypatch)
    monkeypatch.setattr(steam, "api_key", lambda: "")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(steam, "send_friend_message", lambda _c, _s, target, text: sent.append((target, text)) or {"ok": True})

    need = _run(inst.steam_reply_friend(friend="76561198000000001", text="hi"))
    assert need["error"] == "CONFIRM_REQUIRED"
    code = _last_code(inst)
    assert sent == []

    ok = _run(inst.steam_reply_friend(friend="76561198000000001", text="hi", confirm_code=code))
    assert ok["is_error"] is False
    assert sent == [("76561198000000001", "hi")]


def test_reply_friend_partial_errors_surfaced_on_success(monkeypatch) -> None:
    inst, steam = _reply_plugin(monkeypatch)

    def _friends(_key, _sid, _limit, errors):
        errors.append("第 1 批概要获取失败 token=SECRETVALUE")
        return [{"steam_id": "76561198000000001", "name": "Alice"}]

    monkeypatch.setattr(steam, "friend_list", _friends)
    monkeypatch.setattr(steam, "send_friend_message", lambda *a: {"ok": True})

    need = _run(inst.steam_reply_friend(friend="Alice", text="hi"))
    assert need["error"] == "CONFIRM_REQUIRED"
    ok = _run(inst.steam_reply_friend(friend="Alice", text="hi", confirm_code=_last_code(inst)))
    assert ok["is_error"] is False
    assert ok["partial"] is True
    assert "好友概要部分失败" in ok["output"]
    assert "SECRETVALUE" not in ok["output"]
    assert "[redacted]" in ok["warnings"][0]


def test_reply_friend_rejects_over_500_chars() -> None:
    inst = _plugin()
    result = _run(inst.steam_reply_friend(friend="Alice", text="x" * 501))
    assert result["error"] == "TEXT_TOO_LONG"


def test_credentials_partial_success_is_not_overall_error(monkeypatch, tmp_path) -> None:
    """Key 成功、Cookie 失败：明确保留 Key 成功结果，不把整体报成失败。"""
    from plugin.plugins.steam_manager import steam

    writes: list[str] = []
    monkeypatch.setattr(steam, "validate_api_key", lambda key: (True, "ok"))
    monkeypatch.setattr(steam, "save_api_key", lambda key, path=None: writes.append(key) or str(tmp_path))
    monkeypatch.setattr(steam, "validate_community_cookie", lambda c: (False, "sessionid 缺失"))
    monkeypatch.setattr(steam, "save_community_cookie", lambda c, path=None: (_ for _ in ()).throw(AssertionError("不应写入")))
    monkeypatch.setattr(steam, "credentials_status", lambda: {"api_key_configured": True, "cookie_configured": False, "can_use_friends": True, "can_reply_friend": False})
    inst = _plugin()
    result = _run(inst.steam_credentials(mode="yui", api_key="GOODKEY", cookie="BAD", parameters={}))
    assert result["is_error"] is False
    assert result["partial"] is True
    assert "API Key：已加密写入并验证通过" in result["output"]
    assert "Cookie：验证未通过，未写入" in result["output"]
    assert writes == ["GOODKEY"]


def test_open_app_uninstalled_requires_confirm(monkeypatch) -> None:
    """未安装游戏不直接打开搜索第一条，必须先确认候选。"""
    from plugin.plugins.steam_manager import steam

    opened: list[str] = []
    monkeypatch.setattr(steam, "find_game", lambda *a, **k: [])
    monkeypatch.setattr(steam, "search_store", lambda *a, **k: [{"app_id": "123", "name": "Wrong Game"}])
    monkeypatch.setattr(steam, "open_app", lambda _p, app_id, installed: opened.append(app_id) or {"ok": True, "output": "已打开"})
    inst = _plugin()
    inst._steam_path = "S"

    need = _run(inst.steam_open_app(game="My Game"))
    assert need["error"] == "CONFIRM_REQUIRED"
    assert "confirm_token" not in need
    assert opened == []

    ok = _run(inst.steam_open_app(game="My Game", confirm_code=_last_code(inst)))
    assert ok["is_error"] is False
    assert opened == ["123"]


def test_reply_friend_confirm_code_is_one_time_and_bound(monkeypatch) -> None:
    """确认码与好友/消息绑定、只能使用一次，不能拿旧码发另一条消息。"""
    inst, steam = _reply_plugin(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(steam, "friend_list", lambda *a: [
        {"steam_id": "76561198000000001", "name": "Alice Smith"},
    ])
    monkeypatch.setattr(steam, "send_friend_message", lambda _c, _s, target, text: sent.append(text) or {"ok": True})

    need = _run(inst.steam_reply_friend(friend="Alice", text="first"))
    assert need["error"] == "CONFIRM_REQUIRED"
    code = _last_code(inst)

    mismatch = _run(inst.steam_reply_friend(friend="Alice", text="second", confirm_code=code))
    assert mismatch["error"] == "CONFIRM_CODE_INVALID"
    assert sent == []

    replay = _run(inst.steam_reply_friend(friend="Alice", text="first", confirm_code=code))
    assert replay["error"] == "CONFIRM_CODE_INVALID"
    assert sent == []


def test_entry_run_forwards_confirm_code(monkeypatch) -> None:
    """走 run 入口时也必须能把确认码传给 reply_friend。"""
    inst, steam = _reply_plugin(monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(steam, "friend_list", lambda *a: [
        {"steam_id": "76561198000000001", "name": "Alice Smith"},
    ])
    monkeypatch.setattr(steam, "send_friend_message", lambda _c, _s, target, text: sent.append(text) or {"ok": True})

    need = _run(inst.entry_run(action="reply", friend="Alice", text="hi", parameters={}))
    assert need["error"] == "CONFIRM_REQUIRED"
    ok = _run(inst.entry_run(action="reply", friend="Alice", text="hi", confirm_code=_last_code(inst), parameters={}))
    assert ok["is_error"] is False
    assert sent == ["hi"]


def test_bool_params_reject_string_false() -> None:
    """字符串 "false" 不能因为 bool("false") 反而开启接管。"""
    inst = _plugin()
    result = _run(inst.take_control(on="false", parameters={}))
    assert result["is_error"] is False
    assert inst._state.control_enabled is False

    entry = _run(inst.entry_run(action="control", on="false", parameters={}))
    assert entry["is_error"] is False
    assert inst._state.control_enabled is False


def test_shutdown_clears_all_frames(monkeypatch) -> None:
    inst = _plugin()
    from plugin.plugins.steam_manager import control

    class _Watcher:
        stopped = False

        def stop(self):
            self.stopped = True

    watcher = _Watcher()
    inst._watcher = watcher
    calls: list[tuple] = []
    monkeypatch.setattr(
        control, "cleanup_old_frames", lambda *args: calls.append(args) or 0
    )
    result = _run(inst.on_shutdown())
    assert result["status"] == "shutdown"
    assert watcher.stopped is True
    assert calls == [(None, 0, 0.0)]
    assert inst._state.watch_enabled is False
    assert inst._state.control_enabled is False
