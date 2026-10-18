"""steam_manager — Steam 客户端与游戏库管理（Windows）。

功能：
- 检测 Steam 客户端安装路径与运行状态
- 解析本地游戏库（libraryfolders.vdf + appmanifest_*.acf）
- 通过 steam.exe -applaunch 启动已安装游戏

安全边界：只读本地库元数据 + 按用户明确指令启动游戏；
不读取账号、凭据、云存档等敏感数据；启动前必须确认游戏存在。
"""

from __future__ import annotations

import os
import re
import subprocess

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 常见 Steam 安装路径（注册表失败时的兜底）
_FALLBACK_PATHS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
)

# StateFlags 位含义（Steam 官方）
_FLAG_FULLY_INSTALLED = 4  # 已完整安装
_FLAG_UPDATE_REQUIRED = 2  # 需要更新


def _state_flags_text(flags: int) -> str:
    """将 StateFlags 数字转成可读状态描述。"""
    if flags & _FLAG_FULLY_INSTALLED:
        return "已安装"
    if flags & _FLAG_UPDATE_REQUIRED:
        return "需要更新"
    if flags == 0:
        return "未安装"
    return f"状态码 {flags}"


# ══════════ VDF/ACF 解析（纯函数） ══════════

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """解析 Valve VDF/ACF 文本为嵌套 dict。

    兼容 libraryfolders.vdf 与 appmanifest_*.acf 的通用结构：
    "key" "value"、嵌套 "block" { ... }，支持转义引号。
    """
    root: dict = {}
    stack: list[dict] = [root]
    pending_key: str | None = None

    for match in _TOKEN_RE.finditer(text or ""):
        value, brace = match.group(1), match.group(2)
        if value is not None:
            value = value.replace('\\"', '"').replace("\\\\", "\\")
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None
        elif brace == "{":
            block: dict = {}
            if pending_key is not None:
                stack[-1][pending_key] = block
                pending_key = None
            stack.append(block)
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
    return root


# ══════════ Steam 路径与状态 ══════════


def find_steam_path() -> str | None:
    """定位 Steam 安装目录；找不到返回 None。"""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value = winreg.QueryValueEx(key, "SteamPath")[0]
                if value and os.path.isdir(value):
                    return value
        except OSError:
            pass
    for candidate in _FALLBACK_PATHS:
        if os.path.isdir(candidate):
            return candidate
    return None


def steam_running() -> bool:
    """检测 Steam 客户端进程是否在运行。"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
            capture_output=True, text=True, timeout=10.0,
            creationflags=_CREATE_NO_WINDOW,
        ).stdout
        return "steam.exe" in (out or "")
    except (OSError, subprocess.SubprocessError):
        return False


# ══════════ 游戏库扫描 ══════════


def _collect_library_roots(steam_path: str) -> list[str]:
    """收集主库与全部扩展库根目录（去重）。"""
    roots = [steam_path]
    vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf):
        return roots
    try:
        with open(vdf, encoding="utf-8", errors="replace") as fh:
            data = parse_vdf(fh.read())
    except OSError:
        return roots
    libs = data.get("libraryfolders", {})
    for key, value in libs.items():
        if isinstance(value, dict):
            path = value.get("path")
            if isinstance(path, str) and os.path.isdir(path):
                roots.append(path)
        elif isinstance(value, str) and key.isdigit() and os.path.isdir(value):
            roots.append(value)
    return roots


def scan_games(steam_path: str, limit: int = 100) -> list[dict]:
    """扫描所有库中的 appmanifest，返回已安装游戏列表。"""
    games: list[dict] = []
    seen: set[str] = set()
    for root in _collect_library_roots(steam_path):
        apps_dir = os.path.join(root, "steamapps")
        if not os.path.isdir(apps_dir):
            continue
        for filename in os.listdir(apps_dir):
            if not (filename.startswith("appmanifest_") and filename.endswith(".acf")):
                continue
            app_id = filename[len("appmanifest_"):-len(".acf")]
            if not app_id.isdigit() or app_id in seen:
                continue
            seen.add(app_id)
            acf_path = os.path.join(apps_dir, filename)
            try:
                with open(acf_path, encoding="utf-8", errors="replace") as fh:
                    state = parse_vdf(fh.read()).get("AppState", {})
            except OSError:
                continue
            name = str(state.get("name") or filename)
            size = 0
            try:
                size = int(state.get("SizeOnDisk") or 0)
            except (TypeError, ValueError):
                size = 0
            flags = 0
            try:
                flags = int(state.get("StateFlags") or 0)
            except (TypeError, ValueError):
                flags = 0
            games.append(
                {
                    "app_id": app_id,
                    "name": name,
                    "size_mb": round(size / 1048576, 1) if size else 0,
                    "installed": bool(flags & _FLAG_FULLY_INSTALLED),
                    "state": _state_flags_text(flags),
                }
            )
            if len(games) >= limit:
                return games
    return games


def find_game(steam_path: str, query: str, limit: int = 100) -> list[dict]:
    """按名称模糊匹配游戏（返回全部匹配项，供调用方决策）。"""
    query = (query or "").strip().lower()
    games = scan_games(steam_path, limit=limit)
    if not query:
        return games
    if query.isdigit():
        return [g for g in games if g["app_id"] == query]
    return [g for g in games if query in g["name"].lower()]


# ══════════ 启动游戏 ══════════


def launch_game(steam_path: str, app_id: str) -> dict:
    """通过 steam.exe -applaunch 启动指定 AppID 的游戏。

    返回 {"ok": bool, ...} 信封；steam.exe 不存在时给出明确错误。
    """
    app_id = (app_id or "").strip()
    if not app_id.isdigit():
        return {"ok": False, "error": "INVALID_APP_ID", "output": "AppID 必须是数字。"}
    exe = os.path.join(steam_path, "steam.exe")
    if not os.path.isfile(exe):
        return {
            "ok": False,
            "error": "STEAM_EXE_NOT_FOUND",
            "output": f"在 {steam_path} 下未找到 steam.exe，无法启动游戏。",
        }
    try:
        subprocess.Popen(
            [exe, "-applaunch", app_id],
            creationflags=_CREATE_NO_WINDOW,
        )
        return {
            "ok": True,
            "output": f"已通过 Steam 启动游戏（AppID {app_id}）。如未登录 Steam 或游戏未安装，请先完成相应操作。",
        }
    except OSError as exc:
        return {"ok": False, "error": "LAUNCH_FAILED", "output": f"启动游戏失败：{exc}"}
