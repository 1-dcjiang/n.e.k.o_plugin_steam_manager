# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库（Windows），并充当**游戏助手**：读画面、跟踪行为、给建议、可授权代打。

## 工具

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status()` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games(keyword, limit)` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game(game)` | 按名称或 AppID 启动已安装游戏（经 steam.exe -applaunch） |

### 游戏助手（v0.2.0 新增）

| 工具 | 说明 |
|---|---|
| `game_capture()` | 截取当前前台游戏窗口画面（BMP 文件），返回窗口/进程/截图路径 |
| `watch_game(on, interval)` | 开启/关闭后台画面监控：周期性截帧并记录行为时间线 |
| `game_state_summary()` | 汇总当前局面：监控/接管状态、当前窗口、最近截图与近期行为记录，供 YUI 给出建议 |
| `take_control(on)` | **玩家显式授权** YUI 接管键鼠（默认关闭）；随时可交还 |
| `game_action(action, x, y, key, text)` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 游戏助手使用流程（示例）

1. 用户：帮我盯着这局游戏
   - YUI：`watch_game(on=true, interval=3)` → 开始后台截帧
2. 用户：现在什么局面？给点建议
   - YUI：`game_state_summary()` + `game_capture()` → 基于窗口与画面给建议
3. 用户：你来帮我操作
   - YUI：确认窗口在场后 `take_control(on=true)` → 授权 YUI 模拟键鼠
   - YUI：`game_action(action="click", x=..., y=...)` 操作游戏
4. 用户：停止接管
   - YUI：`take_control(on=false)` → 立即交还

## 安全边界（重要）

- Steam 侧：只读取本地库元数据；启动游戏必须来自用户明确指令
- 监控：后台截帧**默认关闭**，用户要求跟踪时才开启（间隔最小 1 秒，仅保留最近 20 帧，自动清理）
- 接管：键鼠模拟**默认关闭**；开启前提是有最近的窗口画面；仅作用于当前前台游戏窗口；每次动作都记录进时间线；用户可随时要求交还
- 不读取账号、凭据、云存档、聊天等敏感数据；不修改 Steam 配置

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 实现说明

- 全部使用 Python 标准库 + ctypes（GDI 截屏存 BMP、SendInput/mouse_event/keybd_event 模拟输入），无第三方依赖
- 截图文件保存于 `%TEMP%\steam_manager\frames\`，随监控滚动清理
- 非 Windows 环境：相关工具返回明确提示，不报错崩溃
