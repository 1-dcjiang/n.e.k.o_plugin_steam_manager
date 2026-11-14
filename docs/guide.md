# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库（Windows），并充当**游戏助手**：读画面、跟踪行为、给建议、可授权代打；
v0.3.0 起同时充当 **Steam 账号/商店助手**：账户信息、好友列表、回复好友、查价格、查促销、打开任意游戏。

## 工具

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status()` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games(keyword, limit)` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game(game)` | 按名称或 AppID 启动已安装游戏（经 steam.exe -applaunch） |

### 账号 / 好友 / 商店（v0.3.0 新增）

| 工具 | 说明 | 凭证 |
|---|---|---|
| `steam_account(steam_id)` | 读取账户信息：本地昵称+SteamID；配置 Key 后含在线状态/国家/资料页 | 可选 STEAM_API_KEY |
| `steam_friends(steam_id, name_filter)` | 好友列表：昵称/SteamID/在线状态，支持昵称过滤 | 需要 STEAM_API_KEY |
| `steam_reply_friend(friend, text)` | 给好友发消息（steamcommunity 网页聊天，最长 500 字） | 需要 STEAM_API_KEY + steam_cookie.txt |
| `steam_game_price(game)` | 查询游戏国区价格（现价/原价/折扣），名称或 AppID | 无需凭证 |
| `steam_promotions(limit)` | 查询商店当前特惠/促销游戏（国区首页特色分类） | 无需凭证 |
| `steam_open_app(game)` | 打开任意游戏/应用：已安装直接启动；未安装打开商店页 | 无需凭证 |

### 游戏助手（v0.2.0 新增）

| 工具 | 说明 |
|---|---|
| `game_capture()` | 截取当前前台游戏窗口画面（BMP 文件），返回窗口/进程/截图路径 |
| `watch_game(on, interval)` | 开启/关闭后台画面监控：周期性截帧并记录行为时间线 |
| `game_state_summary()` | 汇总当前局面：监控/接管状态、当前窗口、最近截图与近期行为记录，供 YUI 给出建议 |
| `take_control(on)` | **玩家显式授权** YUI 接管键鼠（默认关闭）；随时可交还 |
| `game_action(action, x, y, key, text)` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 可选凭证配置

- **STEAM_API_KEY**（账户信息增强 / 好友列表 / 回复好友时按昵称解析）：环境变量 `STEAM_API_KEY`，
  或调用 `steam_credentials` 手动粘贴 Key。凭证以 Windows DPAPI 加密保存在
  `%LOCALAPPDATA%\N.E.K.O\steam_manager`，不会写入插件源码目录。
- **steamcommunity Cookie**（回复好友必需）：登录 `https://steamcommunity.com/` 后，把含 `sessionid`
  与 `steamLoginSecure`/`steamLogin` 的 Cookie 字符串手动发给 YUI 配置。插件**不会**自动读取或解密浏览器 Cookie。

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

- Steam 侧：只读取本地库元数据与账号昵称；启动/打开游戏必须来自用户明确指令
- 凭证：API Key / cookie 只读取不输出到对话；回复好友仅在用户明确要求时发送
- 监控：后台截帧**默认关闭**，用户要求跟踪时才开启（间隔最小 1 秒，仅保留最近 40 个帧文件，自动清理 PNG/BMP）
- 接管：键鼠模拟**默认关闭**；开启前提是有最近的窗口画面；仅作用于当前前台游戏窗口；每次动作都记录进时间线；用户可随时要求交还
- 游戏窗口：默认匹配标题含 `civilization vi`，可用 `steam.game_window_title` 改为其他游戏
- 不自动探测浏览器 Cookie；凭证只能手动粘贴配置，并以 DPAPI 加密保存；验证通过后才写入
- 回复好友按精确昵称或 17 位 SteamID 匹配；同名/同 ID 多个时要求指定，纯数字输入不做子串猜人
- 昵称仅子串匹配、好友概要部分失败（精确同名也可能漏在失败批次）、或无 API Key 直接按 SteamID 发送时，会先返回候选并要求 `confirm_token` 一次性确认
- 确认令牌与好友/消息绑定，5 分钟内有效且只能用一次；入口 `run` 同样透传
- 未安装游戏打开商店页前同样需要 `confirm_token`，不会直接打开搜索第一条
- `take_control` 等布尔参数显式解析 `true/false/1/0`，字符串 `"false"` 不会误开启
- 不读取密码、令牌、云存档、聊天记录等敏感数据；不修改 Steam 配置

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 实现说明

- 全部使用 Python 标准库 + ctypes（GDI 截屏存 PNG，失败回退 BMP；SendInput 发送 Unicode 文本），无第三方依赖
- 截图文件保存于 `%TEMP%\steam_manager\frames\`，随监控滚动清理；TEMP/TMP 缺失或会退化为当前工作目录时，回退到用户主目录下的 `.steam_manager\tmp`
- 多显示器：鼠标绝对坐标使用 `SM_*VIRTUALSCREEN` 虚拟桌面边界，不再被钳到主屏
- 非 Windows 环境：相关工具返回明确提示，不报错崩溃
