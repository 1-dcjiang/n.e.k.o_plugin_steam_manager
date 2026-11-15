"""steam_manager 游戏助手单元测试：状态机、授权门禁、坐标换算、BMP 输出。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

import struct
import time


def _fake_window(title: str = "Test Game", ts: float | None = None) -> dict:
    return {
        "hwnd": 12345,
        "title": title,
        "pid": 999,
        "process": "game.exe",
        "rect": (100, 100, 500, 400),
        "ts": ts if ts is not None else time.time(),
    }


# ══════════ ControlState 状态机 ══════════


def test_state_initial_flags() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    assert state.watch_enabled is False
    assert state.control_enabled is False
    assert len(state.frames) == 0


def test_watch_toggle_records_events() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.enable_watch()
    assert state.watch_enabled is True
    state.note_frame(_fake_window(), r"C:\tmp\frame.bmp")
    assert len(state.frames) == 1
    state.disable_watch()
    assert state.watch_enabled is False
    events = [t["event"] for t in state.timeline]
    assert "watch_start" in events
    assert "watch_stop" in events


def test_control_requires_window_evidence() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    target = _fake_window(ts=time.time())
    # 无任何帧 → 拒绝开启接管
    assert state.enable_control(target) is False
    assert state.control_enabled is False
    # 有目标窗口的最近帧 → 允许开启
    state.note_frame(target, r"C:\tmp\frame.bmp")
    assert state.enable_control(target) is True
    assert state.control_enabled is True
    # 交还
    state.disable_control()
    assert state.control_enabled is False


def test_control_rejected_with_stale_frame() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    stale = _fake_window(ts=time.time() - 3600)
    state.note_frame(stale, r"C:\tmp\old.bmp")
    assert state.enable_control(stale) is False


def test_recent_window_active() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.note_frame(_fake_window(ts=time.time()), "x.bmp")
    assert state.recent_window_active(within_seconds=10) is True
    state.note_frame(_fake_window(ts=time.time() - 100), "y.bmp")
    assert state.recent_window_active(within_seconds=10) is False


def test_summary_shape() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState()
    state.note_frame(_fake_window(), r"C:\tmp\f.bmp")
    state.note_event("action", "click")
    summary = state.summary()
    assert summary["last_window"]["title"] == "Test Game"
    assert len(summary["frames"]) == 1
    assert summary["timeline"][0]["event"] == "action"


def test_frames_ring_buffer_bounded() -> None:
    from plugin.plugins.steam_manager.control import ControlState

    state = ControlState(max_frames=3)
    for i in range(10):
        state.note_frame(_fake_window(title=f"G{i}", ts=time.time()), f"{i}.bmp")
    assert len(state.frames) == 3
    assert state.frames[0]["title"] == "G7"


# ══════════ _parse_vk ══════════


def test_parse_vk() -> None:
    from plugin.plugins.steam_manager.__init__ import _parse_vk

    assert _parse_vk("a") == ord("A")
    assert _parse_vk("5") == 0x35
    assert _parse_vk("65") == 65
    assert _parse_vk("0") == 0x30
    assert _parse_vk("300") is None
    assert _parse_vk("") is None
    assert _parse_vk("abc") is None
    assert _parse_vk("中") is None


# ══════════ 坐标换算 ══════════


def test_window_client_to_screen() -> None:
    from plugin.plugins.steam_manager.control import window_client_to_screen

    win = _fake_window()  # rect (100,100)-(500,400) → w=400 h=300
    sx, sy = window_client_to_screen(win, 500, 500)
    assert sx == 300
    assert sy == 250
    sx, sy = window_client_to_screen(win, 0, 0)
    assert (sx, sy) == (100, 100)
    sx, sy = window_client_to_screen(win, 1000, 1000)
    assert (sx, sy) == (499, 399)  # right/bottom 是排他边界，不能落在窗口外


# ══════════ BMP 写入（字节校验） ══════════


def test_write_bmp_header_and_size(tmp_path) -> None:
    from plugin.plugins.steam_manager.control import _write_bmp

    # 4x2 的 32 位 BGRA 像素（每行 16 字节）
    pixels = bytes(range(4 * 4 * 2))
    path = _write_bmp(str(tmp_path), 4, 2, pixels)
    assert path and path.endswith(".bmp")
    data = open(path, "rb").read()
    assert data[:2] == b"BM"
    offset, width, height, planes, bpp = struct.unpack_from("<IiiHH", data, 14)
    assert offset == 40
    assert width == 4 and height == 2 and planes == 1 and bpp == 32
    file_size = len(data)
    # 4x2 的 32 位 BGRA：每行 4px*4B=16，2 行 → 54 + 16*2 = 86（数据与头部声明的
    # biSizeImage 一致，PIL/视觉可正常解码；此前头部声明 32bit 却只写 3 字节/像素
    # 的 24bit 数据，文件长度与头不符导致读图端报 truncated）
    assert file_size == 54 + 16 * 2

# ══════════ v0.3.1：帧目录清理回归测试 ══════════


def test_cleanup_old_frames_keeps_recent(tmp_path, monkeypatch) -> None:
    """FrameWatcher 定期清理：PNG/BMP 混合目录只保留最近 keep 个帧文件。"""
    import os

    from plugin.plugins.steam_manager import control

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    names = []
    for i in range(1, 11):
        name = f"frame_{i:04d}.png" if i % 2 == 0 else f"frame_{i:04d}.bmp"
        path = frame_dir / name
        path.write_bytes(b"\x00" * 10)
        os.utime(path, (1000 + i, 1000 + i))
        names.append(name)
    monkeypatch.setattr(control, "_FRAME_DIR", str(frame_dir))
    state = control.ControlState()
    watcher = control.FrameWatcher(state, interval=9.0)
    watcher._cleanup_old_frames(keep=3)
    remaining = sorted(f.name for f in frame_dir.iterdir())
    assert remaining == sorted(names[-3:])


def test_cleanup_keeps_recent_files_even_beyond_keep(tmp_path) -> None:
    """连续截帧时，超出 keep 的新帧不能被立刻删掉（视觉通道可能还在读）。"""
    import os
    import time

    from plugin.plugins.steam_manager import control

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for i in range(6):
        path = frame_dir / f"frame_{i}.png"
        path.write_bytes(b"\x00")
        now = time.time()
        os.utime(path, (now, now))
    assert control.cleanup_old_frames(str(frame_dir), keep=2, min_age=120) == 0
    assert len(list(frame_dir.iterdir())) == 6


def test_cleanup_deletes_old_files_beyond_keep(tmp_path) -> None:
    import os
    import time

    from plugin.plugins.steam_manager import control

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for i in range(6):
        path = frame_dir / f"frame_{i}.png"
        path.write_bytes(b"\x00")
        old = time.time() - 300
        os.utime(path, (old, old))
    assert control.cleanup_old_frames(str(frame_dir), keep=2, min_age=120) == 4
    assert len(list(frame_dir.iterdir())) == 2


# ══════════ v0.4.1：Unicode 输入 / 鼠标边界 / 通用窗口 ══════════


def test_unicode_units_supports_chinese_and_symbols() -> None:
    from plugin.plugins.steam_manager.control import _unicode_units

    assert _unicode_units("A!") == [ord("A"), ord("!")]
    assert _unicode_units("中") == [0x4E2D]
    assert len(_unicode_units("😀")) == 2  # 代理对


def test_type_text_uses_sendinput_unicode(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    captured: list[list[tuple[int, int]]] = []

    class _FakeUser32:
        def SendInput(self, count, events, size):
            captured.append([(events[i].ki.wScan, events[i].ki.dwFlags) for i in range(count)])
            return count

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    assert control.type_text("A中") == 2
    events = captured[0]
    assert len(events) == 4
    assert events[0] == (ord("A"), control._KEYEVENTF_UNICODE)
    assert events[1][1] == control._KEYEVENTF_UNICODE | control._KEYEVENTF_KEYUP
    assert events[2][0] == 0x4E2D


def test_mouse_click_reports_partial_sendinput(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[tuple[int, list[int]]] = []

    class _FakeUser32:
        def SendInput(self, count, events, size):
            calls.append((count, [events[i].mi.dwFlags for i in range(count)]))
            return 2 if len(calls) == 1 else count  # 首次只注入移动 + 左键按下

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    monkeypatch.setattr(control, "_screen_bounds", lambda: (0, 0, 1920, 1080))
    assert control.mouse_click_abs(100, 100) is False
    assert len(calls) == 2  # 必须补偿性抬起，不能留下左键按下状态
    assert calls[1] == (1, [control._MOUSEEVENTF_LEFTUP])


def test_press_key_reports_partial_sendinput(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    calls: list[tuple[int, list[int]]] = []

    class _FakeUser32:
        def SendInput(self, count, events, size):
            calls.append((count, [events[i].ki.dwFlags for i in range(count)]))
            return 1 if len(calls) == 1 else count  # 首次只注入按下

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    assert control.press_key(0x41) is False
    assert len(calls) == 2
    assert calls[1] == (1, [control._KEYEVENTF_KEYUP])
    assert control.press_key(0) is False


def test_mouse_click_abs_clamps_to_screen(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    captured: list[tuple[int, int, int]] = []

    class _FakeUser32:
        def mouse_event(self, flags, dx, dy, data, extra):
            if flags & control._MOUSEEVENTF_MOVE:
                captured.append((flags, dx, dy))

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    monkeypatch.setattr(control, "_screen_bounds", lambda: (0, 0, 1920, 1080))
    control.mouse_click_abs(-50, 2000)
    assert captured[0][1:] == (0, 65535)
    assert captured[0][0] & control._MOUSEEVENTF_VIRTUALDESK
    control.mouse_click_abs(5000, -10)
    assert captured[-1][1:] == (65535, 0)


def test_find_window_by_title_empty_without_user32(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control, "USER32", None)
    assert control.find_window_by_title("civilization vi") is None
    assert control.find_window_by_title("") is None


def test_find_window_by_title_skips_browser_with_same_title(monkeypatch) -> None:
    """标题命中浏览器标签页时不能被当成游戏；枚举全部后按进程过滤再取第一个。"""
    from plugin.plugins.steam_manager import control

    class _FakeUser32:
        titles = {11: "Civilization VI - Chrome", 22: "Civilization VI"}
        pids = {11: 1001, 22: 1002}
        order = [11, 22]

        def IsWindowVisible(self, hwnd):
            return True

        def EnumWindows(self, callback, _lparam):
            for hwnd in self.order:
                callback(hwnd, 0)
            return 1

        def GetWindowTextW(self, hwnd, buf, _size):
            buf.value = self.titles[hwnd]
            return len(buf.value)

        def GetWindowLongW(self, _hwnd, _index):
            return 0

        def GetWindowRect(self, hwnd, ptr):
            rect = ptr._obj
            rect.left, rect.top, rect.right, rect.bottom = (0, 0, 800, 600)
            return 1

        def GetWindowThreadProcessId(self, hwnd, ptr):
            ptr._obj.value = self.pids[hwnd]
            return 1

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    monkeypatch.setattr(control, "_process_name",
                        lambda pid: "chrome.exe" if pid == 1001 else "game.exe")
    found = control.find_window_by_title("civilization vi")
    assert found is not None
    assert found["hwnd"] == 22
    assert found["process"] == "game.exe"


# ══════════ v0.4.2：Win32 结构/签名、多显示器与线程竞态 ══════════


def test_input_struct_matches_win32_layout() -> None:
    """INPUT 必须包含鼠标/键盘/硬件三个 union 成员，否则 64 位下 SendInput 尺寸错误。"""
    import ctypes
    import os
    import sys

    import pytest
    from plugin.plugins.steam_manager import control

    if os.name != "nt":
        pytest.skip("Win32 结构体尺寸只在 Windows ABI 下有意义")
    expected = 40 if sys.maxsize > 2**32 else 28
    assert ctypes.sizeof(control._INPUT) == expected
    assert ctypes.sizeof(control._INPUTUNION) == expected - 8 if sys.maxsize > 2**32 else 24


def test_type_text_passes_full_input_size(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    sizes: list[int] = []

    class _FakeUser32:
        def SendInput(self, count, events, size):
            sizes.append(size)
            return count

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    assert control.type_text("A中") == 2
    assert sizes == [control.ctypes.sizeof(control._INPUT)]


def test_screen_bounds_uses_virtual_desktop(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    values = {76: -1920, 77: 0, 78: 3840, 79: 1080}

    class _FakeUser32:
        def GetSystemMetrics(self, index):
            return values[index]

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    assert control._screen_bounds() == (-1920, 0, 3840, 1080)


def test_mouse_click_abs_maps_negative_virtual_origin(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    captured: list[tuple[int, int, int]] = []

    class _FakeUser32:
        def mouse_event(self, flags, dx, dy, data, extra):
            if flags & control._MOUSEEVENTF_MOVE:
                captured.append((flags, dx, dy))

    monkeypatch.setattr(control, "USER32", _FakeUser32())
    monkeypatch.setattr(control, "_screen_bounds", lambda: (-1920, 0, 3840, 1080))
    control.mouse_click_abs(-1920, 0)
    control.mouse_click_abs(1919, 1079)
    assert captured[0][1:] == (0, 0)
    assert captured[1][1:] == (65535, 65535)


def test_cleanup_survives_single_stat_failure(tmp_path, monkeypatch) -> None:
    """一个帧文件 stat 失败时，其余旧帧仍应被清理，而不是整批中止。"""
    import os

    from plugin.plugins.steam_manager import control

    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    names = []
    for i in range(1, 8):
        path = frame_dir / f"frame_{i:04d}.png"
        path.write_bytes(b"x")
        os.utime(path, (1000 + i, 1000 + i))
        names.append(path.name)

    real_getmtime = os.path.getmtime
    broken = frame_dir / names[-1]

    def fake_getmtime(path):
        if os.path.abspath(path) == os.path.abspath(broken):
            raise OSError("stat failed")
        return real_getmtime(path)

    monkeypatch.setattr(control.os.path, "getmtime", fake_getmtime)
    removed = control.cleanup_old_frames(str(frame_dir), keep=2)
    assert removed == 4
    assert set(f.name for f in frame_dir.iterdir()) == {names[-1], names[-2], names[-3]}


def test_frame_watcher_refuses_second_thread_after_stop_timeout(monkeypatch) -> None:
    from plugin.plugins.steam_manager import control

    state = control.ControlState()
    watcher = control.FrameWatcher(state, interval=9.0)

    class _StuckThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

        def start(self):
            raise AssertionError("不应启动第二个线程")

    watcher._thread = _StuckThread()
    watcher._stop.set()
    assert watcher.start() is False  # 不得创建/启动新线程，且必须如实返回 False
    assert watcher._thread is not None


def test_default_frame_dir_never_falls_back_to_cwd(monkeypatch, tmp_path) -> None:
    from plugin.plugins.steam_manager import control

    monkeypatch.setattr(control.os, "environ", {})
    monkeypatch.setattr(control.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(control.os, "getcwd", lambda: str(tmp_path))
    monkeypatch.setattr(control.os.path, "expanduser", lambda _p: str(tmp_path / "home"))
    result = control._default_frame_dir()
    assert result != str(tmp_path / "steam_manager" / "frames")
