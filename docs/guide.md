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
| `game_capture()` | 截取配置中的游戏窗口画面（PNG 预览 + 原始 BMP；被遮挡时 PrintWindow 直抓本体），返回窗口/进程/截图路径 |
| `watch_game(on, interval)` | 开启/关闭后台画面监控：周期性截帧并记录行为时间线 |
| `game_state_summary()` | 汇总当前局面：监控/接管状态、当前窗口、最近截图与近期行为记录，供 YUI 给出建议 |
| `take_control(on, grant_code)` | **玩家显式授权** YUI 接管键鼠（默认关闭）；首次开启把 6 位授权码推送到用户界面；随时可交还 |
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
- 监控：后台截帧**默认关闭**，用户要求跟踪时才开启（间隔最小 1 秒，仅保留最近 40 个帧文件，且 120 秒内不删，避免删掉视觉通道正在读取的截图）
- 接管：键鼠模拟**默认关闭**；开启前提是有目标游戏窗口的最近画面，且目标必须在前台；浏览器/终端/资源管理器进程一律拒绝；找不到标题/进程匹配的游戏窗口时直接拒绝，不回退前台窗口；接管只作用于绑定的那个游戏窗口，每次动作前复核前台，窗口切走/最小化立即失效；每次动作都记录进时间线；用户可随时要求交还，civ6 运营锁期间也能立即停车
- 游戏窗口：默认匹配标题含 `civilization vi`；可用 `steam.game_window_title` 改标题，并用 `steam.game_process` 指定精确进程名（如 `factorio.exe`，不区分大小写）。标题键存在且留空时不做 PrintWindow 直抓，只截取进程匹配的前台窗口；标题和 `game_process` 都为空时拒绝截图/监控/接管，避免把任意前台窗口发给模型。v0.4.15 起只配置 `game_process`（标题留空）也能接管：按该进程的可见窗口绑定目标，仍要求目标在前台且有最近画面，找不到就拒绝、不回退前台窗口
- 点击：`x/y` 是游戏**客户区**相对千分比（0–1000），不包含标题栏和边框；通过真实 `GetClientRect` + `ClientToScreen` 换算，换算失败时拒绝点击。截图也使用同一个客户区矩形（BitBlt 用客户区屏幕原点，PrintWindow 用 `PW_CLIENTONLY`），模型按截图估坐标再点击不会整体偏移；客户区宽或高为 0 时按“不可用”拒绝截图/点击，不再返回 1×1 空白图
- DPI：不在导入插件时修改进程 DPI Awareness，而是在首次真实截图或键鼠输入前延迟、加锁、幂等设置；设置前先查询现状，宿主已经感知就直接算成功，不再误报“没有可用 API”
- 不自动探测浏览器 Cookie；凭证只能手动粘贴配置，并以 DPAPI 加密保存；验证通过后才写入。手动把 Key/Cookie 发给 YUI 时，凭证会经过宿主模型与对话链路，DPAPI 只保护落盘数据；请仅在可信宿主/模型环境中配置
- Cookie 校验/发送请求只在 `steamcommunity.com`、`steampowered.com` 及其子域的 HTTPS 跳转间保留 Cookie，跨域或降级 HTTP 会移除 Cookie/Origin；聊天响应限制为 64 KiB
- 回复好友按精确昵称或 17 位 SteamID 匹配；同名/同 ID 多个时要求指定，纯数字输入不做子串猜人
- 昵称仅子串匹配、好友概要部分失败（精确同名也可能漏在失败批次）、或无 API Key 直接按 SteamID 发送时，插件会把 6 位确认码推送到用户界面，用户读出后才能发送；`steam.require_friend_confirm=true` 可强制所有发送都确认
- 确认码与好友/消息绑定，5 分钟内有效且只能用一次；入口 `run` 用 `confirm_code` 透传
- 未安装游戏打开商店页前同样需要确认码，不会直接打开搜索第一条
- Cookie 校验必须落到真实个人资料页（`/profiles/<数字>` 或 `/id/<名字>`）并读回身份字段；发消息会核对 Cookie 本人 SteamID 与本地账号一致，并带 `sessionid`
- Steam 旧网页聊天接口的 401/403 诊断为 Cookie 失效，404/405/410 诊断为接口不可用/可能下线；其他错误不统一归因于 Cookie。本轮未做带真实凭证的实机聊天验证
- `take_control` 等布尔参数显式解析 `true/false/1/0`，字符串 `"false"` 不会误开启
- 不读取密码、令牌、云存档、聊天记录等敏感数据；不修改 Steam 配置

## 其他游戏配置示例

```toml
[steam]
game_window_title = "factorio"
game_process = "factorio.exe"
# 可选：所有好友发送均要求确认
require_friend_confirm = false
```

完整配置注释见仓库根目录的 `config.example.toml`。

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 实现说明

- Win32 窗口、截图与输入调用使用 Python 标准库 `ctypes`；**Pillow（导入名 `PIL`）由 N.E.K.O 宿主提供**，用于 GDI 像素 PNG 编码、BMP→PNG 转换与超大截图 JPEG 缩放；插件不捆绑平台相关的私有 vendor，不可用时 GDI 截图回退为标准库 BMP，BMP 转 PNG 失败会保留 BMP
- 超大截图在无法使用 Pillow 缩图时不能生成视觉 `images` 条目，会记录警告；其他本地游戏库、价格等功能不受影响
- 截图文件保存于 `%TEMP%\steam_manager\frames\`，随监控滚动清理；TEMP/TMP 缺失或会退化为当前工作目录时，回退到用户主目录下的 `.steam_manager\tmp`
- 多显示器：鼠标绝对坐标使用 `SM_*VIRTUALSCREEN` 虚拟桌面边界，不再被钳到主屏
- 非 Windows 环境：相关工具返回明确提示，不报错崩溃
