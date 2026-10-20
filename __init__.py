"""steam_manager — Steam 管理 + 游戏助手。

安全边界：只读本地库元数据；启动游戏需用户明确指令；截图与行为跟踪
仅在用户开启监控后运行；键鼠接管（YUI 操作游戏）默认关闭，必须由
用户显式授权（take_control），且仅作用于当前前台游戏窗口；不读取
账号、凭据、云存档等敏感数据。
"""

from __future__ import annotations

from plugin.sdk.plugin import (
    NekoPluginBase,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from . import control, steam


@neko_plugin
class SteamManagerPlugin(NekoPluginBase):
    """N.E.K.O. 插件：Steam 游戏库管理与游戏助手。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._steam_path: str | None = None
        self._state = control.ControlState()
        self._watcher = control.FrameWatcher(self._state)

    # ══════════ 生命周期 ══════════

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self._steam_path = steam.find_steam_path()
        return {"status": "ready", "plugin": "steam_manager"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._watcher.stop()
        self._state.disable_watch()
        self._state.disable_control()
        return {"status": "shutdown"}

    # ══════════ Steam 基础工具 ══════════

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
        games = steam.find_game(path, keyword, limit=limit)
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

    # ══════════ 游戏助手工具 ══════════

    @llm_tool(
        name="game_capture",
        description=(
            "截取当前前台游戏窗口的画面。何时用：用户问'现在游戏里是什么情况/截个图看看/这局怎么样了'，"
            "或你需要了解当前游戏画面时。返回窗口标题、进程、截图文件路径与时间；"
            "截图是 BMP 文件，路径可直接提供给视觉能力解读或让用户查看。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=30.0,
    )
    async def game_capture(self, **_):
        info = control.get_foreground_window_info()
        if not info:
            return {"output": "没有检测到前台窗口（可能窗口已最小化）。请确认游戏窗口在前台。", "is_error": True, "error": "NO_WINDOW"}
        path = control.capture_window_bmp(info)
        if not path:
            return {"output": "截图失败：无法读取屏幕区域。", "is_error": True, "error": "CAPTURE_FAILED"}
        self._state.note_frame(info, path)
        self._state.note_event("capture", f"{info['title']} / {info['process']}")
        width = info["rect"][2] - info["rect"][0]
        height = info["rect"][3] - info["rect"][1]
        return {
            "output": (
                f"已截取前台窗口画面：\n窗口：{info['title']}（进程 {info['process']}，PID {info['pid']}）\n"
                f"尺寸：{width}×{height}\n截图路径：{path}\n时间：{info['ts']:.0f}"
            ),
            "is_error": False,
            "screenshot": path,
        }

    @llm_tool(
        name="watch_game",
        description=(
            "开启或关闭后台画面监控。何时用：用户要求'帮我盯着游戏/实时跟踪/看他打到哪了'，"
            "或需要持续了解游戏进展时。参数 on：true 开启、false 关闭；interval：可选，截帧间隔秒数（默认 3，最小 1）。"
            "开启后插件会周期性截取前台窗口画面并记录行为时间线，供 game_state_summary 查询。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "true 开启监控，false 关闭"},
                "interval": {"type": "integer", "description": "可选：截帧间隔秒数，默认 3"},
            },
        },
        timeout=20.0,
    )
    async def watch_game(self, *, on: bool = False, interval: int = 0, **_):
        params = _.get("parameters") or {}
        on = bool(params.get("on", on))
        try:
            interval = int(interval or params.get("interval") or 3)
        except (TypeError, ValueError):
            interval = 3
        if on:
            self._watcher = control.FrameWatcher(self._state, interval=interval)
            self._watcher.start()
            self._state.enable_watch()
            return {"output": f"后台画面监控已开启（每 {max(1, interval)} 秒截一帧）。用 game_state_summary 查看进展，用 watch_game(on=false) 关闭。", "is_error": False}
        self._watcher.stop()
        self._state.disable_watch()
        return {"output": "后台画面监控已关闭。", "is_error": False}

    @llm_tool(
        name="game_state_summary",
        description=(
            "汇总游戏当前状态与近期行为时间线。何时用：用户问'现在什么局面/他刚才做了什么/这局进展如何'，"
            "或你需要基于画面与操作历史给出建议时。返回监控/接管状态、当前窗口、最近截图与行为记录。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=20.0,
    )
    async def game_state_summary(self, **_):
        summary = self._state.summary()
        lines = []
        lines.append(f"监控状态：{'开启' if summary['watch_enabled'] else '关闭'} | 接管状态：{'YUI 已接管' if summary['control_enabled'] else '未接管'}")
        win = summary.get("last_window")
        if win:
            lines.append(f"当前窗口：{win.get('title', '')}（进程 {win.get('process', '')}，PID {win.get('pid', 0)}）")
        else:
            lines.append("当前窗口：无最近记录（可先 game_capture 截一帧）")
        frames = summary.get("frames", [])
        if frames:
            latest = frames[-1]
            lines.append(f"最近截图：{latest.get('path', '')}（{latest.get('ts', 0):.0f}）")
            lines.append(f"已记录画面帧数：{len(frames)}")
        timeline = summary.get("timeline", [])
        if timeline:
            recent = timeline[-8:]
            lines.append("近期行为记录：")
            for item in recent:
                lines.append(f"  [{item.get('ts', 0):.0f}] {item.get('event', '')} {item.get('detail', '')}")
        return {"output": "\n".join(lines), "is_error": False, "summary": summary}

    @llm_tool(
        name="take_control",
        description=(
            "授权或交还 YUI 对游戏窗口的键鼠控制。何时用：用户明确说'让 YUI 接管/你来玩/帮我操作'时开启，"
            "用户说'停止接管/交还控制/我自己来'时关闭。"
            "安全：开启前必须先有最近的窗口画面（先 game_capture 或开启 watch_game）；"
            "接管仅作用于当前前台游戏窗口；用户随时可用本工具或对话直接交还。"
            "参数 on：true 开启、false 交还。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "on": {"type": "boolean", "description": "true 开启接管，false 交还"},
            },
        },
        timeout=20.0,
    )
    async def take_control(self, *, on: bool = False, **_):
        params = _.get("parameters") or {}
        on = bool(params.get("on", on))
        if not on:
            self._state.disable_control()
            return {"output": "已交还控制，YUI 不再模拟键鼠操作游戏。", "is_error": False}
        if not self._state.enable_control():
            return {"output": "无法开启接管：没有最近的游戏窗口画面（先 game_capture 截一帧，或开启 watch_game 监控）。", "is_error": True, "error": "NO_WINDOW_EVIDENCE"}
        return {
            "output": "接管已开启：YUI 现在可以模拟键鼠操作当前游戏窗口。玩家随时说「停止接管」即可交还。",
            "is_error": False,
        }

    @llm_tool(
        name="game_action",
        description=(
            "在已授权的游戏窗口上执行一次键鼠动作。何时用：YUI 已获得接管授权（take_control on）后，"
            "需要点击画面或按键操作游戏时。"
            "参数 action：click（点击，需 x/y，窗口内相对坐标 0-1000）、key（按键，key 为单字母或 1-255 的虚拟键码）、"
            "text（输入文本，text 为 ASCII 内容）；x/y：点击坐标（窗口相对千分比）。"
            "安全：仅当接管开启且窗口仍在场时可用；每次动作都会记录到行为时间线。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["click", "key", "text"], "description": "动作类型"},
                "x": {"type": "integer", "description": "click 的窗口相对 X（0-1000）"},
                "y": {"type": "integer", "description": "click 的窗口相对 Y（0-1000）"},
                "key": {"type": "string", "description": "key 的单字母或虚拟键码（1-255）"},
                "text": {"type": "string", "description": "text 的 ASCII 内容"},
            },
            "required": ["action"],
        },
        timeout=20.0,
    )
    async def game_action(self, *, action: str = "", x: int = 0, y: int = 0, key: str = "", text: str = "", **_):
        params = _.get("parameters") or {}
        action = (action or params.get("action") or "").strip().lower()
        try:
            x = int(x if x else params.get("x") or 0)
            y = int(y if y else params.get("y") or 0)
        except (TypeError, ValueError):
            x, y = 0, 0
        key = (key or params.get("key") or "").strip()
        text = (text or params.get("text") or "").strip()
        if not self._state.control_enabled:
            return {"output": "接管未开启，无法执行操作。请先让用户明确授权：take_control(on=true)。", "is_error": True, "error": "CONTROL_DISABLED"}
        if not self._state.recent_window_active(within_seconds=15):
            return {"output": "最近 15 秒内没有新的窗口画面，可能游戏不在前台或已切换。请先 game_capture 确认窗口在场。", "is_error": True, "error": "WINDOW_MISSING"}
        win = self._state.last_window
        if not win:
            return {"output": "缺少窗口信息，无法执行操作。请先 game_capture。", "is_error": True, "error": "NO_WINDOW"}
        if action == "click":
            sx, sy = control.window_client_to_screen(win, max(0, min(1000, x)), max(0, min(1000, y)))
            control.mouse_click_abs(sx, sy)
            self._state.note_event("action", f"click ({x},{y}) → 屏幕 ({sx},{sy}) @ {win.get('title', '')}")
            return {"output": f"已点击窗口内 ({x},{y})（屏幕 {sx},{sy}）。", "is_error": False}
        if action == "key":
            vk = _parse_vk(key)
            if vk is None:
                return {"output": "key 参数无效：请输入单字母（如 'a'）或 1-255 的虚拟键码。", "is_error": True, "error": "BAD_KEY"}
            control.press_key(vk)
            self._state.note_event("action", f"key {key}")
            return {"output": f"已按键：{key}。", "is_error": False}
        if action == "text":
            if len(text) > 100:
                return {"output": "文本过长（上限 100 字符）。", "is_error": True, "error": "TEXT_TOO_LONG"}
            control.type_text(text)
            self._state.note_event("action", f"text {text}")
            return {"output": f"已输入文本：{text}。", "is_error": False}
        return {"output": "action 无效：可选 click / key / text。", "is_error": True, "error": "BAD_ACTION"}

    # ══════════ 自动路由入口（plugin_entry：TaskExecutor 评估可见） ══════════
    # v0.2.0 只注册了 llm_tool、无 plugin_entry，导致市场面板"入口点: 0"、
    # 评估侧无法路由到本插件（Steam 管理与游戏助手功能实际不可用）。
    # v0.2.1 注册 run 通用入口修复（方法名不能以下划线开头，bili_viewer v0.6.22 教训）。

    @plugin_entry(
        id="run",
        name="Steam管理",
        description=(
            "Steam管理（steam_manager）：管理本地 Steam 客户端与游戏库，并充当游戏助手。"
            "用户说'Steam 开着吗/我 Steam 里有什么游戏/打开/启动 <游戏>/现在游戏什么情况/截个图看看/帮我盯着游戏/"
            "让 YUI 接管/你来玩/停止接管'等 Steam 与游戏相关请求时使用。"
            "操作：status（查客户端状态与路径）/ list（列游戏库，可按名称过滤）/ launch（启动游戏，需精确名称或 AppID）/ "
            "capture（截取当前游戏画面）/ watch（开启/关闭后台画面监控）/ summary（汇总局面与行为时间线）/ "
            "control（开启/交还键鼠接管，需用户显式授权且仅作用于前台游戏窗口）/ action（click/key/text 操作，需先授权）。"
            "安全边界：只读本地库元数据；键鼠接管默认关闭、必须用户明确授权，仅作用于当前前台游戏窗口，"
            "不读取账号与凭据。回复纪律：直接给出查询结果或操作确认，不加多余话术，不编造游戏状态。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型：status / list / launch / capture / watch / summary / control / action",
                },
                "game": {"type": "string", "description": "启动游戏：名称或 AppID（action=launch 时用）"},
                "keyword": {"type": "string", "description": "列表过滤关键词（action=list 时用）"},
                "limit": {"type": "integer", "description": "列表条数上限（action=list 时用）"},
                "on": {"type": "boolean", "description": "watch / control 的开关：true 开，false 关"},
                "interval": {"type": "integer", "description": "watch 的截帧间隔秒数"},
                "click_x": {"type": "integer", "description": "action=click 的窗口相对 X（0-1000）"},
                "click_y": {"type": "integer", "description": "action=click 的窗口相对 Y（0-1000）"},
                "key": {"type": "string", "description": "action=key 的单字母或虚拟键码"},
                "text": {"type": "string", "description": "action=text 的 ASCII 文本"},
            },
        },
        timeout=30.0,
    )
    async def entry_run(self, **kwargs):
        params = kwargs.get("parameters") or {}
        action = str(kwargs.get("action") or params.get("action") or "").strip().lower()
        game = str(kwargs.get("game") or params.get("game") or "")
        keyword = str(kwargs.get("keyword") or params.get("keyword") or "")
        try:
            limit = int(kwargs.get("limit") or params.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        on = bool(kwargs.get("on", params.get("on", False)))
        try:
            interval = int(kwargs.get("interval") or params.get("interval") or 0)
        except (TypeError, ValueError):
            interval = 0
        try:
            click_x = int(kwargs.get("click_x") or params.get("click_x") or 0)
            click_y = int(kwargs.get("click_y") or params.get("click_y") or 0)
        except (TypeError, ValueError):
            click_x, click_y = 0, 0
        key = str(kwargs.get("key") or params.get("key") or "")
        text = str(kwargs.get("text") or params.get("text") or "")
        if action in ("status", ""):
            return await self.steam_status()
        if action in ("list", "games"):
            return await self.list_games(keyword=keyword, limit=limit, parameters={})
        if action in ("launch", "start"):
            return await self.launch_game(game=game, parameters={})
        if action in ("capture", "screenshot"):
            return await self.game_capture()
        if action in ("watch", "monitor"):
            return await self.watch_game(on=on, interval=interval, parameters={})
        if action in ("summary", "state"):
            return await self.game_state_summary()
        if action in ("control", "take_control"):
            return await self.take_control(on=on, parameters={})
        if action in ("action", "operate"):
            return await self.game_action(
                action=str(params.get("sub_action") or params.get("act") or ""),
                x=click_x, y=click_y, key=key, text=text, parameters={},
            )
        return {
            "output": "请指定操作：status/list/launch/capture/watch/summary/control/action。",
            "is_error": True,
            "error": "NO_ACTION",
        }


def _parse_vk(key: str) -> int | None:
    """把按键名解析为虚拟键码：单字母/数字用 ASCII 码，多位数字用 VK 码。"""
    if not key:
        return None
    if len(key) == 1:
        code = ord(key.upper())
        if 48 <= code <= 90:  # 数字或字母
            return code
    if key.isdigit():
        vk = int(key)
        return vk if 1 <= vk <= 255 else None
    return None
