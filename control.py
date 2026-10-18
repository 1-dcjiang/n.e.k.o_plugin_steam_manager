"""steam_manager 游戏助手核心：画面读取、行为跟踪、键鼠接管（Windows）。

设计原则：
- 全部使用 Python 标准库 + ctypes（不依赖第三方包），截图走 GDI（BMP），
  输入走 SendInput / mouse_event / keybd_event
- 状态机（ControlState）与硬件操作分离，便于单元测试
- 接管（键鼠模拟）默认关闭，必须由用户显式授权；game_action 仅在
  control_enabled 且窗口仍在场时可用；每次动作都写入行为时间线
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import threading
import time
from collections import deque

USER32 = ctypes.windll.user32
GDI32 = ctypes.windll.gdi32
KERNEL32 = ctypes.windll.kernel32
PSAPI = ctypes.windll.psapi

# ══════════ Win32 常量 ══════════

_PW_RENDERFULLCONTENT = 2
_BI_RGB = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000
_KEYEVENTF_KEYUP = 0x0002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GWL_STYLE = -16
_WS_MINIMIZE = 0x20000000

_MAX_FRAMES = 20
_MAX_TIMELINE = 120
_MIN_INTERVAL = 1.0

_FRAME_DIR = os.path.join(os.environ.get("TEMP", "."), "steam_manager", "frames")


class _Rect(wt.RECT):
    pass


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wt.DWORD * 3)]


# ══════════ 窗口与进程信息 ══════════


def _safe_call(fn, *args, default=None):
    try:
        return fn(*args)
    except (OSError, AttributeError, ctypes.WinError):
        return default


def get_foreground_window_info() -> dict | None:
    """返回前台窗口信息：{title, pid, process, rect, ts}；无窗口返回 None。"""
    hwnd = USER32.GetForegroundWindow()
    if not hwnd:
        return None
    title_buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, title_buf, 512)
    rect = _Rect()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    style = USER32.GetWindowLongW(hwnd, _GWL_STYLE)
    if style & _WS_MINIMIZE:
        return None
    pid = wt.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = _process_name(pid.value)
    return {
        "hwnd": hwnd,
        "title": title_buf.value or "",
        "pid": pid.value,
        "process": process,
        "rect": (rect.left, rect.top, rect.right, rect.bottom),
        "ts": time.time(),
    }


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = KERNEL32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if KERNEL32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        name_buf = ctypes.create_unicode_buffer(256)
        if PSAPI.GetModuleBaseNameW(handle, None, name_buf, 256):
            return name_buf.value
    finally:
        KERNEL32.CloseHandle(handle)
    return ""


# ══════════ 截屏（GDI → BMP） ══════════


def capture_window_bmp(info: dict, save_dir: str = _FRAME_DIR) -> str | None:
    """截取窗口所在屏幕区域，保存为 BMP，返回文件路径；失败返回 None。"""
    left, top, right, bottom = info["rect"]
    width = max(1, right - left)
    height = max(1, bottom - top)
    hdc_screen = USER32.GetDC(None)
    hdc_mem = GDI32.CreateCompatibleDC(hdc_screen)
    hbmp = GDI32.CreateCompatibleBitmap(hdc_screen, width, height)
    old = GDI32.SelectObject(hdc_mem, hbmp)
    ok = GDI32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, left, top, 0x00CC0020)
    if not ok:
        GDI32.SelectObject(hdc_mem, old)
        GDI32.DeleteObject(hbmp)
        GDI32.DeleteDC(hdc_mem)
        USER32.ReleaseDC(None, hdc_screen)
        return None
    # GetDIBits 取像素
    bmi = _BitmapInfo()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = _BI_RGB
    row_size = (width * 4 + 3) & ~3
    buf = ctypes.create_string_buffer(row_size * height)
    GDI32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), 0)
    GDI32.SelectObject(hdc_mem, old)
    GDI32.DeleteObject(hbmp)
    GDI32.DeleteDC(hdc_mem)
    USER32.ReleaseDC(None, hdc_screen)
    return _write_bmp(save_dir, width, height, buf.raw)


def _write_bmp(save_dir: str, width: int, height: int, dib_pixels: bytes) -> str | None:
    os.makedirs(save_dir, exist_ok=True)
    row_size = (width * 4 + 3) & ~3
    data_size = row_size * height
    file_size = 54 + data_size
    header = bytearray()
    header += b"BM"
    header += struct.pack("<IHHI", file_size, 0, 0, 54)
    header += struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, data_size, 0, 0, 0, 0)
    # BGRA → BGR（去掉 alpha，每像素 3 字节）
    pixels = bytearray()
    for y in range(height):
        row = dib_pixels[y * row_size:(y + 1) * row_size]
        for x in range(width):
            i = x * 4
            pixels += row[i:i + 3]
        pixels += b"\x00" * ((row_size - width * 3) % 4)
    path = os.path.join(save_dir, f"frame_{int(time.time() * 1000)}.bmp")
    try:
        with open(path, "wb") as fh:
            fh.write(bytes(header) + bytes(pixels))
    except OSError:
        return None
    return path


# ══════════ 键鼠模拟 ══════════


def _screen_size() -> tuple[int, int]:
    return (USER32.GetSystemMetrics(0), USER32.GetSystemMetrics(1))


def mouse_click_abs(screen_x: int, screen_y: int) -> None:
    """在屏幕绝对坐标点击左键。"""
    sw, sh = _screen_size()
    dx = int(screen_x * 65535 // max(1, sw - 1))
    dy = int(screen_y * 65535 // max(1, sh - 1))
    extra = ctypes.c_ulong(0)
    USER32.mouse_event(_MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_MOVE, dx, dy, 0, extra)
    USER32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, extra)
    USER32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, extra)


def press_key(vk: int) -> None:
    """按下并抬起一个虚拟键。"""
    extra = ctypes.c_ulong(0)
    USER32.keybd_event(vk, 0, 0, extra)
    USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, extra)


def type_text(text: str) -> None:
    """逐字符输入 ASCII 文本（非 ASCII 会跳过并计数）。"""
    typed = 0
    for ch in text:
        code = ord(ch)
        if code > 127:
            continue
        press_key(code)
        typed += 1


def window_client_to_screen(info: dict, rel_x: float, rel_y: float) -> tuple[int, int]:
    """把窗口内相对坐标（0-1000 千分比）换算为屏幕绝对坐标。"""
    left, top, right, bottom = info["rect"]
    w = max(1, right - left)
    h = max(1, bottom - top)
    return left + int(w * rel_x / 1000), top + int(h * rel_y / 1000)


# ══════════ 状态机（纯逻辑，可测） ══════════


class ControlState:
    """监控与接管状态：时间线、帧记录、授权门禁。"""

    def __init__(self, max_frames: int = _MAX_FRAMES, max_timeline: int = _MAX_TIMELINE) -> None:
        self.watch_enabled = False
        self.control_enabled = False
        self.last_window: dict | None = None
        self.frames: deque = deque(maxlen=max_frames)
        self.timeline: deque = deque(maxlen=max_timeline)
        self._lock = threading.Lock()

    # ── 时间线 ──

    def note_event(self, event: str, detail: str = "") -> None:
        with self._lock:
            self.timeline.append({"ts": time.time(), "event": event, "detail": detail})

    def note_frame(self, info: dict, path: str) -> None:
        with self._lock:
            self.last_window = info
            self.frames.append(
                {"ts": info.get("ts", time.time()), "path": path, "title": info.get("title", ""),
                 "pid": info.get("pid", 0), "process": info.get("process", "")}
            )

    # ── 开关 ──

    def enable_watch(self) -> None:
        if not self.watch_enabled:
            self.watch_enabled = True
            self.note_event("watch_start", "后台画面监控已开启")

    def disable_watch(self) -> None:
        if self.watch_enabled:
            self.watch_enabled = False
            self.note_event("watch_stop", "后台画面监控已关闭")

    def enable_control(self) -> bool:
        """开启接管。要求：窗口在场且有最近帧证据。"""
        if not self.recent_window_active(within_seconds=30):
            return False
        if not self.control_enabled:
            self.control_enabled = True
            self.note_event("control_on", "YUI 接管已授权（键鼠模拟）")
        return True

    def disable_control(self) -> None:
        if self.control_enabled:
            self.control_enabled = False
            self.note_event("control_off", "YUI 接管已交还")

    # ── 查询 ──

    def recent_window_active(self, within_seconds: float = 10.0) -> bool:
        with self._lock:
            if not self.frames:
                return False
            latest = self.frames[-1].get("ts", 0)
            return (time.time() - latest) <= within_seconds

    def summary(self) -> dict:
        with self._lock:
            return {
                "watch_enabled": self.watch_enabled,
                "control_enabled": self.control_enabled,
                "last_window": dict(self.last_window) if self.last_window else None,
                "frames": [dict(f) for f in self.frames],
                "timeline": [dict(t) for t in self.timeline],
            }


# ══════════ 后台监控线程 ══════════


class FrameWatcher:
    """周期截取前台窗口画面的后台线程。"""

    def __init__(self, state: ControlState, interval: float = 3.0) -> None:
        self._state = state
        self._interval = max(_MIN_INTERVAL, float(interval))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="steam-manager-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            info = get_foreground_window_info()
            if info:
                path = capture_window_bmp(info)
                if path:
                    self._state.note_frame(info, path)
                    self._state.note_event("frame", f"{info.get('title', '')} / {info.get('process', '')}")
            self._stop.wait(self._interval)
