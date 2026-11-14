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
import logging
import os
import struct
import tempfile
import threading
import time
import uuid
from collections import deque

_logger = logging.getLogger("N.E.K.O.Plugin_steam_manager.control")

USER32 = GDI32 = KERNEL32 = PSAPI = None
if os.name == "nt":
    try:
        USER32 = ctypes.windll.user32
        GDI32 = ctypes.windll.gdi32
        KERNEL32 = ctypes.windll.kernel32
        PSAPI = ctypes.windll.psapi
    except (AttributeError, OSError):
        USER32 = GDI32 = KERNEL32 = PSAPI = None

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

_KEYEVENTF_UNICODE = 0x0004
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_MAX_FRAMES = 20
_MAX_TIMELINE = 120
_MIN_INTERVAL = 1.0
_MAX_FRAME_FILES = 40
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _default_frame_dir() -> str:
    """帧目录：优先系统临时目录；TEMP/TMP 缺失或退化成 CWD 时不写当前目录。"""
    base = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    try:
        same_as_cwd = os.path.abspath(base) == os.path.abspath(os.getcwd())
    except OSError:
        same_as_cwd = False
    if not base or same_as_cwd or not os.path.isdir(base):
        home = os.path.expanduser("~")
        if home and home != "~":
            base = os.path.join(home, ".steam_manager", "tmp")
        else:
            base = tempfile.gettempdir()
    return os.path.join(base, "steam_manager", "frames")


_FRAME_DIR = _default_frame_dir()


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


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


_WNDENUMPROC = (ctypes.WINFUNCTYPE if hasattr(ctypes, "WINFUNCTYPE") else ctypes.CFUNCTYPE)(
    wt.BOOL, wt.HWND, wt.LPARAM
)


def _set_sig(fn, argtypes, restype):
    try:
        fn.argtypes = argtypes
        fn.restype = restype
    except (AttributeError, TypeError) as exc:  # 某些精简 DLL 代理不暴露函数签名
        _logger.debug("设置 Win32 签名失败：%s", exc)


def _configure_win32() -> None:
    """显式声明 Win32 签名。64 位下默认 c_int 返回值会把 HWND/HDC 句柄截断成 32 位。"""
    if USER32 is not None:
        # 进程级 DPI awareness：不声明时高分屏下 GetWindowRect/鼠标坐标会被系统虚拟化缩放，
        # 截图尺寸与实际点击位置错位（v0.4.6）。
        for name, value in (("SetProcessDpiAwarenessContext", -4), ("SetProcessDPIAware", None)):
            fn = getattr(USER32, name, None)
            if fn is not None:
                try:
                    if value is None:
                        fn()
                    else:
                        fn(ctypes.c_void_p(value))
                    break
                except (OSError, AttributeError, ctypes.ArgumentError):
                    continue
        _set_sig(USER32.GetForegroundWindow, [], wt.HWND)
        _set_sig(USER32.GetWindowTextW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int)
        # RECT 指针用 c_void_p：模块被重复加载时 POINTER(_Rect) 会绑定到旧类，
        # 传 byref(新 _Rect()) 反而报 LP__Rect 类型不匹配。
        _set_sig(USER32.GetWindowRect, [wt.HWND, ctypes.c_void_p], wt.BOOL)
        _set_sig(USER32.GetWindowLongW, [wt.HWND, ctypes.c_int], ctypes.c_long)
        _set_sig(USER32.GetWindowThreadProcessId, [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD)
        _set_sig(USER32.EnumWindows, [_WNDENUMPROC, wt.LPARAM], wt.BOOL)
        _set_sig(USER32.IsWindowVisible, [wt.HWND], wt.BOOL)
        _set_sig(USER32.GetDC, [wt.HWND], ctypes.c_void_p)
        _set_sig(USER32.GetWindowDC, [wt.HWND], ctypes.c_void_p)
        _set_sig(USER32.ReleaseDC, [wt.HWND, ctypes.c_void_p], ctypes.c_int)
        _set_sig(USER32.PrintWindow, [wt.HWND, ctypes.c_void_p, wt.UINT], wt.BOOL)
        _set_sig(USER32.GetSystemMetrics, [ctypes.c_int], ctypes.c_int)
        _set_sig(
            USER32.mouse_event,
            [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t],
            None,
        )
        _set_sig(USER32.keybd_event, [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t], None)
        _set_sig(
            USER32.SendInput,
            [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int],
            wt.UINT,
        )
    if GDI32 is not None:
        _set_sig(GDI32.CreateCompatibleDC, [ctypes.c_void_p], ctypes.c_void_p)
        _set_sig(
            GDI32.CreateCompatibleBitmap,
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int],
            ctypes.c_void_p,
        )
        _set_sig(GDI32.SelectObject, [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p)
        _set_sig(
            GDI32.BitBlt,
            [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wt.DWORD,
            ],
            wt.BOOL,
        )
        _set_sig(
            GDI32.GetDIBits,
            [
                ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT, ctypes.c_void_p,
                ctypes.POINTER(_BitmapInfo), wt.UINT,
            ],
            ctypes.c_int,
        )
        _set_sig(GDI32.DeleteObject, [ctypes.c_void_p], wt.BOOL)
        _set_sig(GDI32.DeleteDC, [ctypes.c_void_p], wt.BOOL)
    if KERNEL32 is not None:
        _set_sig(KERNEL32.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)
        _set_sig(
            KERNEL32.QueryFullProcessImageNameW,
            [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)],
            wt.BOOL,
        )
        _set_sig(KERNEL32.CloseHandle, [wt.HANDLE], wt.BOOL)
    if PSAPI is not None:
        _set_sig(
            PSAPI.GetModuleBaseNameW,
            [wt.HANDLE, ctypes.c_void_p, wt.LPWSTR, wt.DWORD],
            wt.DWORD,
        )


_configure_win32()


# ══════════ 窗口与进程信息 ══════════


def _safe_call(fn, *args, default=None):
    try:
        return fn(*args)
    except (OSError, AttributeError, ValueError, TypeError, ctypes.ArgumentError):
        return default


def get_foreground_window_info() -> dict | None:
    """返回前台窗口信息：{title, pid, process, rect, ts}；无窗口或非 Windows 返回 None。"""
    if USER32 is None:
        return None
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
    if not pid or KERNEL32 is None:
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
    """截取窗口所在屏幕区域，保存为 BMP，返回文件路径；失败或非 Windows 返回 None。"""
    if USER32 is None or GDI32 is None:
        return None
    left, top, right, bottom = info["rect"]
    width = max(1, right - left)
    height = max(1, bottom - top)
    hdc_screen = USER32.GetDC(None)
    if not hdc_screen:
        return None
    hdc_mem = GDI32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        USER32.ReleaseDC(None, hdc_screen)
        return None
    hbmp = GDI32.CreateCompatibleBitmap(hdc_screen, width, height)
    if not hbmp:
        GDI32.DeleteDC(hdc_mem)
        USER32.ReleaseDC(None, hdc_screen)
        return None
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
    copied = GDI32.GetDIBits(
        hdc_mem, hbmp, 0, height, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(bmi), 0
    )
    GDI32.SelectObject(hdc_mem, old)
    GDI32.DeleteObject(hbmp)
    GDI32.DeleteDC(hdc_mem)
    USER32.ReleaseDC(None, hdc_screen)
    if copied != height:
        _logger.warning("GetDIBits 只复制了 %s/%s 行，放弃本帧", copied, height)
        return None
    return _write_png(save_dir, width, height, buf.raw)


def _write_png(save_dir: str, width: int, height: int, dib_pixels: bytes) -> str | None:
    """把 BGRA DIB 像素直接存为 PNG（PIL，视觉模型可读）。

    BMP 通道此前手写文件头，biSizeImage 用了 32bit 行对齐值而实际像素是 3 字节行，
    长度不一致导致 BMP 损坏（PIL/YUI 视觉报 truncated，'看不到画面'的根因）。
    PIL 写 PNG 由库保证头/数据一致。PIL 不可用时回退修正后的 _write_bmp。
    """
    try:
        from PIL import Image

        os.makedirs(save_dir, exist_ok=True)
        img = Image.frombytes("RGBA", (width, height), dib_pixels, "raw", "BGRA").convert("RGB")
        path = os.path.join(save_dir, f"frame_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png")
        img.save(path, "PNG")
        cleanup_old_frames(save_dir)
        return path
    except Exception as exc:
        _logger.warning("PNG 写入失败，回退 BMP：%s", exc)
        return _write_bmp(save_dir, width, height, dib_pixels)


def _write_bmp(save_dir: str, width: int, height: int, dib_pixels: bytes) -> str | None:
    os.makedirs(save_dir, exist_ok=True)
    row_size = (width * 4 + 3) & ~3
    data_size = row_size * height
    file_size = 54 + data_size
    header = bytearray()
    header += b"BM"
    header += struct.pack("<IHHI", file_size, 0, 0, 54)
    header += struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, data_size, 0, 0, 0, 0)
    # DIB 是自上而下，BMP 正高度要求自下而上：逐行倒序写入，避免回退图上下颠倒。
    rows = [
        dib_pixels[row * row_size:(row + 1) * row_size]
        for row in range(height - 1, -1, -1)
    ]
    path = os.path.join(save_dir, f"frame_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.bmp")
    try:
        with open(path, "wb") as fh:
            fh.write(bytes(header) + b"".join(rows))
    except OSError as exc:
        _logger.warning("BMP 写入失败：%s", exc)
        return None
    cleanup_old_frames(save_dir)
    return path


def find_window_by_title(title_hint: str) -> dict | None:
    """查找标题包含 title_hint 的可见窗口（不区分大小写），返回窗口信息。

    默认提示词为 Civilization VI，可通过插件配置 steam.game_window_title 改为其他游戏。
    """
    if USER32 is None:
        return None
    hint = (title_hint or "").strip().casefold()
    if not hint:
        return None
    found: list[int] = []

    @_WNDENUMPROC
    def _enum(hwnd, _lparam):
        if USER32.IsWindowVisible(hwnd):
            title_buf = ctypes.create_unicode_buffer(512)
            USER32.GetWindowTextW(hwnd, title_buf, 512)
            title = title_buf.value or ""
            if hint in title.casefold():
                found.append(hwnd)
                return False
        return True

    USER32.EnumWindows(_enum, 0)
    if not found:
        return None
    hwnd = found[0]
    rect = _Rect()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    style = USER32.GetWindowLongW(hwnd, _GWL_STYLE)
    if style & _WS_MINIMIZE:
        return None  # 最小化窗口 PrintWindow 拿不到有效画面
    title_buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, title_buf, 512)
    pid = wt.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {
        "hwnd": hwnd,
        "title": title_buf.value or "",
        "pid": pid.value,
        "process": _process_name(pid.value),
        "rect": (rect.left, rect.top, rect.right, rect.bottom),
        "ts": time.time(),
    }


def find_civ6_window() -> dict | None:
    """兼容旧调用：查找文明6 主窗口。"""
    return find_window_by_title("civilization vi")


def capture_window_printwindow(info: dict, save_dir: str = _FRAME_DIR) -> str | None:
    """PrintWindow 直抓窗口内容，保存为 BMP，返回文件路径；失败返回 None。

    与 capture_window_bmp（屏幕区域截图，被遮挡时截到最上层窗口）的区别：
    PrintWindow 向目标窗口请求绘制，拿到的永远是窗口本体画面——
    游戏被聊天/终端窗口完全遮挡时也能拿到游戏画面。"""
    if USER32 is None or GDI32 is None:
        return None
    hwnd = info.get("hwnd")
    if not hwnd:
        return None
    left, top, right, bottom = info["rect"]
    width = max(1, right - left)
    height = max(1, bottom - top)
    hdc_win = USER32.GetWindowDC(hwnd)
    if not hdc_win:
        return None
    try:
        hdc_mem = GDI32.CreateCompatibleDC(hdc_win)
        if not hdc_mem:
            return None
        try:
            hbmp = GDI32.CreateCompatibleBitmap(hdc_win, width, height)
            if not hbmp:
                return None
            try:
                old = GDI32.SelectObject(hdc_mem, hbmp)
                PW_RENDERFULLCONTENT = 2
                if not USER32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
                    GDI32.SelectObject(hdc_mem, old)
                    return None
                bmi = _BitmapInfo()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = _BI_RGB
                row_size = (width * 4 + 3) & ~3
                buf = ctypes.create_string_buffer(row_size * height)
                if not GDI32.GetDIBits(
                    hdc_mem, hbmp, 0, height, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(bmi), 0
                ):
                    GDI32.SelectObject(hdc_mem, old)
                    return None
                GDI32.SelectObject(hdc_mem, old)
                return _write_png(save_dir, width, height, buf.raw)
            finally:
                GDI32.DeleteObject(hbmp)
        finally:
            GDI32.DeleteDC(hdc_mem)
    finally:
        USER32.ReleaseDC(hwnd, hdc_win)


# ══════════ 键鼠模拟 ══════════


def _screen_size() -> tuple[int, int]:
    """主屏尺寸（保留旧接口）；多屏绝对坐标请用 _screen_bounds。"""
    return (USER32.GetSystemMetrics(0), USER32.GetSystemMetrics(1))


def _screen_bounds() -> tuple[int, int, int, int]:
    """虚拟桌面边界 (x, y, width, height)，覆盖所有显示器；失败时退回主屏。"""
    if USER32 is None:
        return (0, 0, 1, 1)
    try:
        vx = USER32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        vy = USER32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        vw = USER32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        vh = USER32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    except (AttributeError, OSError, ValueError, TypeError):
        vx = vy = 0
        vw = vh = 0
    if vw <= 0 or vh <= 0:
        sw, sh = _screen_size()
        vx = vy = 0
        vw, vh = max(1, sw), max(1, sh)
    return vx, vy, vw, vh


def mouse_click_abs(screen_x: int, screen_y: int) -> bool:
    """在屏幕绝对坐标点击左键；返回是否确认注入 3 个事件（UIPI 拦截时为 False）。"""
    if USER32 is None:
        return False
    vx, vy, vw, vh = _screen_bounds()
    sx = max(vx, min(vx + vw - 1, int(screen_x)))
    sy = max(vy, min(vy + vh - 1, int(screen_y)))
    dx = int((sx - vx) * 65535 // max(1, vw - 1))
    dy = int((sy - vy) * 65535 // max(1, vh - 1))
    extra = ctypes.c_size_t(0)
    send_input = getattr(USER32, "SendInput", None)
    if send_input is not None:
        events = (_INPUT * 3)()
        for idx, flags in enumerate((
            _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_MOVE | _MOUSEEVENTF_VIRTUALDESK,
            _MOUSEEVENTF_LEFTDOWN,
            _MOUSEEVENTF_LEFTUP,
        )):
            ev = events[idx]
            ev.type = _INPUT_MOUSE
            ev.mi.dx = dx
            ev.mi.dy = dy
            ev.mi.mouseData = 0
            ev.mi.dwFlags = flags
            ev.mi.time = 0
            ev.mi.dwExtraInfo = None
        sent = send_input(3, events, ctypes.sizeof(_INPUT))
        if sent != 3:
            _logger.warning("鼠标 SendInput 只发送了 %s/3 个事件", sent)
            return False
        return True
    # 精简 DLL 代理没有 SendInput 时退回 legacy，无法核对注入数。
    USER32.mouse_event(
        _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_MOVE | _MOUSEEVENTF_VIRTUALDESK, dx, dy, 0, extra
    )
    USER32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, extra)
    USER32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, extra)
    return True


def press_key(vk: int) -> bool:
    """按下并抬起一个虚拟键；返回是否确认注入 2 个事件。"""
    if USER32 is None or not isinstance(vk, int) or not (1 <= vk <= 255):
        return False
    extra = ctypes.c_size_t(0)
    send_input = getattr(USER32, "SendInput", None)
    if send_input is not None:
        events = (_INPUT * 2)()
        for idx, flags in enumerate((0, _KEYEVENTF_KEYUP)):
            ev = events[idx]
            ev.type = _INPUT_KEYBOARD
            ev.ki.wVk = vk
            ev.ki.wScan = 0
            ev.ki.dwFlags = flags
            ev.ki.time = 0
            ev.ki.dwExtraInfo = None
        sent = send_input(2, events, ctypes.sizeof(_INPUT))
        if sent != 2:
            _logger.warning("按键 SendInput 只发送了 %s/2 个事件", sent)
            return False
        return True
    USER32.keybd_event(vk, 0, 0, extra)
    USER32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, extra)
    return True


def _unicode_units(text: str) -> list[int]:
    """把文本转成 SendInput 需要的 UTF-16 code unit 序列（支持中文与代理对）。"""
    units: list[int] = []
    for ch in text:
        raw = ch.encode("utf-16-le")
        for i in range(0, len(raw), 2):
            units.append(int.from_bytes(raw[i:i + 2], "little"))
    return units


def type_text(text: str) -> int:
    """发送 Unicode 按键输入（大写、符号、中文均可），返回字符数。

    此前逐字符 press_key(ord(ch)) 只适用于小写 ASCII：大写缺 Shift、':' 等
    符号的 ASCII 码不是虚拟键码、非 ASCII 直接跳过。改用 KEYEVENTF_UNICODE。
    """
    if USER32 is None or not text:
        return 0
    units = _unicode_units(text)
    if not units:
        return 0
    events = (_INPUT * (len(units) * 2))()
    for idx, unit in enumerate(units):
        for offset, flags in ((0, _KEYEVENTF_UNICODE), (1, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)):
            ev = events[idx * 2 + offset]
            ev.type = _INPUT_KEYBOARD
            ev.ki.wVk = 0
            ev.ki.wScan = unit
            ev.ki.dwFlags = flags
            ev.ki.time = 0
            ev.ki.dwExtraInfo = None
    sent = USER32.SendInput(len(events), events, ctypes.sizeof(_INPUT))
    if sent != len(events):
        _logger.warning("SendInput 只发送了 %s/%s 个事件", sent, len(events))
        return 0
    return len(text)


def window_client_to_screen(info: dict, rel_x: float, rel_y: float) -> tuple[int, int]:
    """把窗口内相对坐标（0-1000 千分比）换算为屏幕绝对坐标（含右下边界 clamp）。"""
    left, top, right, bottom = info["rect"]
    w = max(1, right - left)
    h = max(1, bottom - top)
    rel_x = max(0.0, min(1000.0, float(rel_x)))
    rel_y = max(0.0, min(1000.0, float(rel_y)))
    sx = left + min(w - 1, int(w * rel_x / 1000))
    sy = top + min(h - 1, int(h * rel_y / 1000))
    if USER32 is not None:
        vx, vy, vw, vh = _screen_bounds()
        sx = max(vx, min(vx + vw - 1, sx))
        sy = max(vy, min(vy + vh - 1, sy))
    return sx, sy


# ══════════ 状态机（纯逻辑，可测） ══════════


class ControlState:
    """监控与接管状态：时间线、帧记录、授权门禁。"""

    def __init__(self, max_frames: int = _MAX_FRAMES, max_timeline: int = _MAX_TIMELINE) -> None:
        self.watch_enabled = False
        self.control_enabled = False
        self.last_window: dict | None = None
        self.control_target: dict | None = None
        self.frames: deque = deque(maxlen=max_frames)
        self.timeline: deque = deque(maxlen=max_timeline)
        self._lock = threading.RLock()

    # ── 时间线 ──

    def note_event(self, event: str, detail: str = "") -> None:
        with self._lock:
            self.timeline.append({"ts": time.time(), "event": event, "detail": detail})

    def note_frame(self, info: dict, path: str) -> None:
        with self._lock:
            self.last_window = info
            self.frames.append(
                {"ts": info.get("ts", time.time()), "path": path, "title": info.get("title", ""),
                 "pid": info.get("pid", 0), "hwnd": info.get("hwnd", 0),
                 "process": info.get("process", "")}
            )

    # ── 开关 ──

    def enable_watch(self) -> None:
        with self._lock:
            if not self.watch_enabled:
                self.watch_enabled = True
                self.note_event("watch_start", "后台画面监控已开启")

    def disable_watch(self) -> None:
        with self._lock:
            if self.watch_enabled:
                self.watch_enabled = False
                self.note_event("watch_stop", "后台画面监控已关闭")

    def enable_control(self, target: dict | None = None) -> bool:
        """开启接管。要求：目标窗口已绑定，且该窗口在 30 秒内有帧证据。"""
        with self._lock:
            if not target or not target.get("hwnd"):
                return False
            if not self.recent_window_active(target, within_seconds=30):
                return False
            self.control_target = dict(target)
            if not self.control_enabled:
                self.control_enabled = True
                self.note_event("control_on", "YUI 接管已授权（键鼠模拟）")
            return True

    def disable_control(self) -> None:
        with self._lock:
            if self.control_enabled:
                self.control_enabled = False
                self.note_event("control_off", "YUI 接管已交还")
            self.control_target = None

    # ── 查询 ──

    def recent_window_active(
        self, target: dict | None = None, within_seconds: float = 10.0
    ) -> bool:
        with self._lock:
            if not self.frames:
                return False
            if target and target.get("hwnd"):
                hwnd = target.get("hwnd")
                latest = max(
                    (f.get("ts", 0) for f in self.frames if f.get("hwnd") == hwnd),
                    default=0,
                )
            else:
                latest = self.frames[-1].get("ts", 0)
            return (time.time() - latest) <= within_seconds

    @staticmethod
    def target_matches(target: dict | None, info: dict | None) -> bool:
        """目标窗口是否仍是同一个窗口/进程（hwnd 优先，退化到 pid+标题）。"""
        if not target or not info:
            return False
        if target.get("hwnd") and info.get("hwnd"):
            return target["hwnd"] == info["hwnd"]
        return bool(target.get("pid")) and target.get("pid") == info.get("pid")

    def summary(self) -> dict:
        with self._lock:
            return {
                "watch_enabled": self.watch_enabled,
                "control_enabled": self.control_enabled,
                "last_window": dict(self.last_window) if self.last_window else None,
                "control_target": dict(self.control_target) if self.control_target else None,
                "frames": [dict(f) for f in self.frames],
                "timeline": [dict(t) for t in self.timeline],
            }


# ══════════ 后台监控线程 ══════════


def cleanup_old_frames(
    save_dir: str | None = None, keep: int = _MAX_FRAME_FILES, min_age: float = 120.0
) -> int:
    """删除超出 keep 个且至少 min_age 秒未更新的旧帧，返回删除数。

    v0.4.6 之前只按数量清理：连续截帧时刚返回给视觉通道的路径可能被立刻删掉。
    现在保留最近 keep 个，并给所有帧 120 秒宽限；关停时要全清就显式传 min_age=0。
    """
    directory = save_dir or _FRAME_DIR
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    files = [
        os.path.join(directory, name)
        for name in names
        if name.startswith("frame_") and name.lower().endswith((".png", ".bmp"))
    ]
    # 单个文件 stat 失败不应中断整批清理；失败项跳过，保留原文件。
    dated: list[tuple[float, str]] = []
    for path in files:
        try:
            dated.append((os.path.getmtime(path), path))
        except OSError as exc:
            _logger.debug("读取帧时间失败，跳过清理 %s：%s", path, exc)
    dated.sort(key=lambda item: item[0], reverse=True)
    now = time.time()
    removed = 0
    for mtime, old in dated[max(0, keep):]:
        if now - mtime < min_age:
            continue
        try:
            os.remove(old)
            removed += 1
        except OSError as exc:
            _logger.debug("清理旧帧失败 %s：%s", old, exc)
    return removed


_NON_GAME_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "steamwebhelper.exe", "steam.exe", "explorer.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
    "code.exe", "notepad.exe",
}


def is_game_like(info: dict | None) -> bool:
    """标题可伪造，进程名是硬边界；浏览器/终端/资源管理器不算游戏窗口。"""
    if not info:
        return False
    return str(info.get("process") or "").strip().casefold() not in _NON_GAME_PROCESSES


class FrameWatcher:
    """周期截取目标游戏窗口画面的后台线程。

    v0.4.6：同一进程只允许一个监控线程（类级锁/活动实例），并绑定窗口标题；
    绑定了目标时只记录目标窗口，前台是别的窗口时用 PrintWindow 抓目标本体，
    避免把浏览器/终端画面当成游戏帧，也避免点击落到上层窗口。
    """

    _GLOBAL_LOCK = threading.Lock()
    _ACTIVE: "FrameWatcher | None" = None

    def __init__(self, state: ControlState, interval: float = 3.0, title_hint: str = "") -> None:
        self._state = state
        self._interval = max(_MIN_INTERVAL, float(interval))
        self._title_hint = (title_hint or "").strip()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()

    def start(self) -> None:
        with self._lifecycle:
            with FrameWatcher._GLOBAL_LOCK:
                active = FrameWatcher._ACTIVE
                if active is not None and active is not self:
                    active._stop.set()
                    if active._thread:
                        active._thread.join(timeout=2.0)
                    if active._thread and active._thread.is_alive():
                        _logger.warning("另一个帧监控线程尚未退出，拒绝启动新的监控线程")
                        return
                    FrameWatcher._ACTIVE = None
                if self._thread and self._thread.is_alive():
                    if self._stop.is_set():
                        # 上一次 stop 超时但线程仍在收尾：等它真正退出，绝不并发第二个线程。
                        self._thread.join(timeout=2.0)
                    if self._thread and self._thread.is_alive():
                        _logger.warning("上一帧监控线程尚未退出，拒绝启动第二个线程")
                        return
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._loop, name="steam-manager-watcher", daemon=True
                )
                self._thread.start()
                FrameWatcher._ACTIVE = self

    def stop(self) -> None:
        with self._lifecycle:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=2.0)
                if self._thread.is_alive():
                    _logger.warning("帧监控线程 2s 内未退出，保留引用等待下次 stop/start")
                else:
                    self._thread = None
            with FrameWatcher._GLOBAL_LOCK:
                if FrameWatcher._ACTIVE is self:
                    FrameWatcher._ACTIVE = None

    def _target(self) -> dict | None:
        if self._title_hint:
            return find_window_by_title(self._title_hint)
        return get_foreground_window_info()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                target = self._target()
                if target and not is_game_like(target):
                    # 标题命中的是浏览器/终端：不记帧、不截图，避免把网页当游戏画面。
                    target = None
                if target:
                    fg = get_foreground_window_info()
                    same_window = ControlState.target_matches(target, fg)
                    if same_window:
                        path = capture_window_bmp(target)
                    else:
                        # 目标被遮挡/在后台：PrintWindow 只抓目标窗口本体。
                        path = capture_window_printwindow(target)
                    if path:
                        self._state.note_frame(target, path)
                        self._state.note_event(
                            "frame", f"{target.get('title', '')} / {target.get('process', '')}"
                        )
                self._cleanup_old_frames()
            except Exception as exc:  # 后台线程不能因单帧异常退出
                _logger.warning("帧监控循环异常：%s", exc)
            self._stop.wait(self._interval)

    def _cleanup_old_frames(self, keep: int = _MAX_FRAME_FILES) -> None:
        """兼容旧测试/调用：清理 _FRAME_DIR 中旧帧。"""
        cleanup_old_frames(_FRAME_DIR, keep)
