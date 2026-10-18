"""steam_manager — Steam 管理：查询游戏库、启动游戏、检测客户端状态。

安全边界：只读本地库元数据；启动游戏需用户明确指令且确认游戏存在；
不读取账号/凭据，不修改 Steam 配置。
"""

from __future__ import annotations

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
)

from . import steam


@neko_plugin
class SteamManagerPlugin(NekoPluginBase):
    """N.E.K.O. 插件：Steam 游戏库管理。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._steam_path: str | None = None

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._steam_path = steam.find_steam_path()
        return {"status": "ready", "plugin": "steam_manager"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        return {"status": "shutdown"}

    # ══════════ 工具 ══════════

    @llm_tool(
        name="steam_status",
        description=(
            "查询 Steam 客户端状态。何时用：用户问'Steam 开着吗/Steam 装在哪/检测一下 Steam'等。"
            "返回 Steam 是否运行、安装路径（未安装则说明如何配置）。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=15.0,
    )
    async def steam_status(self, **_):
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录。请确认已安装 Steam 客户端（常见路径 C:\\Program Files (x86)\\Steam）。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        running = steam.steam_running()
        return {
            "output": f"Steam 安装路径：{path}\n客户端运行中：{'是' if running else '否'}",
            "is_error": False,
        }

    @llm_tool(
        name="list_games",
        description=(
            "列出 Steam 本地游戏库中的游戏。何时用：用户问'我 Steam 里有什么游戏/列出游戏/查看游戏库'时。"
            "参数 keyword：可选，按名称模糊过滤（如 'cs' 匹配 Counter-Strike）；limit：可选，返回条数上限（默认 50，最大 200）。"
            "返回游戏的 AppID、名称、占用空间与安装状态。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "可选：按名称模糊过滤"},
                "limit": {"type": "integer", "description": "可选：条数上限，默认 50"},
            },
        },
        timeout=30.0,
    )
    async def list_games(self, *, keyword: str = "", limit: int = 0, **_):
        params = _.get("parameters") or {}
        keyword = (keyword or params.get("keyword") or "").strip()
        try:
            limit = int(limit or params.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录，无法读取游戏库。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        games = steam.find_game(path, keyword)
        installed = [g for g in games if g["installed"]]
        if not installed:
            return {"output": f"未找到匹配的已安装游戏（关键词：{keyword or '全部'}）。", "is_error": True, "error": "NO_MATCH"}
        shown = installed[:limit]
        lines = [f"{g['name']}（AppID {g['app_id']}，{g['size_mb']} MB，{g['state']}）" for g in shown]
        head = f"共找到 {len(installed)} 个已安装游戏，显示前 {len(shown)} 个："
        return {"output": head + "\n" + "\n".join(lines), "is_error": False, "games": shown}

    @llm_tool(
        name="launch_game",
        description=(
            "启动 Steam 游戏。何时用：用户明确要求'打开/启动/玩 <游戏名或AppID>'时。"
            "参数 game：游戏名称（支持模糊匹配）或 AppID 数字。"
            "限制：仅当 Steam 已安装且游戏已下载时才能启动；名称匹配到多个游戏时会列出候选让用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "游戏名称或 AppID"},
            },
            "required": ["game"],
        },
        timeout=30.0,
    )
    async def launch_game(self, *, game: str = "", **_):
        params = _.get("parameters") or {}
        game = (game or params.get("game") or "").strip()
        if not game:
            return {"output": "请告诉我你想启动哪个游戏（名称或 AppID）。", "is_error": True, "error": "NO_GAME"}
        path = self._steam_path or steam.find_steam_path()
        if not path:
            return {"output": "未检测到 Steam 安装目录，无法启动游戏。", "is_error": True, "error": "STEAM_NOT_FOUND"}
        matches = steam.find_game(path, game)
        installed = [g for g in matches if g["installed"]]
        if not installed:
            return {"output": f"未找到已安装的游戏「{game}」。可先用 list_games 查看游戏库。", "is_error": True, "error": "NO_MATCH"}
        if len(installed) > 1:
            candidates = "、".join(f"{g['name']}（{g['app_id']}）" for g in installed[:10])
            return {"output": f"「{game}」匹配到多个游戏，请指定精确名称或 AppID：{candidates}", "is_error": True, "error": "AMBIGUOUS"}
        target = installed[0]
        result = steam.launch_game(path, target["app_id"])
        if not result.get("ok"):
            return {"output": result.get("output", "启动失败。"), "is_error": True, "error": result.get("error", "LAUNCH_FAILED")}
        return {"output": f"正在启动「{target['name']}」（AppID {target['app_id']}）。", "is_error": False}
