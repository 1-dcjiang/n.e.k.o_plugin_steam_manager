# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库的 N.E.K.O 插件（Windows），并充当**游戏助手**：读取所玩游戏画面、实时跟踪行为、提供建议，玩家可授权 YUI 接管键鼠代打。
v0.3.0 起新增 **Steam 账号/商店助手**：账户信息、好友列表、回复好友、查价格、查促销、打开任意游戏。
v0.4.1 起凭证改为 DPAPI 加密存储、不再自动探测浏览器 Cookie，并补齐重试、部分失败提示与 Unicode 输入。
v0.4.2 修复 64 位 `INPUT` 结构体尺寸、Win32 句柄签名、多显示器坐标、监控线程竞态、帧清理容错与无 TEMP 时的目录回退。
v0.4.3 按 AppID 打开改为全库扫描，凭证验证通过后才落盘，好友发送改为精确昵称/SteamID 匹配。
v0.4.4 子串匹配与无 Key 直发 SteamID 增加二次确认；凭证部分成功不再报整体失败；未安装游戏打开商店页前需确认。
v0.4.5 确认改为绑定候选的一次性 `confirm_token`，入口透传；布尔参数显式解析；好友部分失败时精确匹配也需确认；凭证模式拒绝 skip 冲突与未知值。
v0.4.6 接管绑定前台游戏窗口并拒绝浏览器/终端进程；确认码/授权码改为推送用户界面、工具结果不返回；civ6 运营锁期间可停车；`SendInput` 失败不报已输入；好友先取全量再过滤；无价格不显示免费；HTTP 重试受 20s 总预算约束；Cookie 校验落到真实资料页并含身份字段；发消息核对 `sessionid` 与发送方 SteamID。
v0.4.7 按自检清单补强：鼠标点击与按键也核对 `SendInput` 注入事件数，被 UIPI 拦截时返回 `INPUT_FAILED`；好友概要查询增加 20s 总预算，超出时保留部分结果并说明剩余人数；帧清理改为“保留最近 40 个帧文件且 120 秒内不删”，避免删掉视觉通道正在读取的截图。
v0.4.8 修复部分键鼠注入后的补偿抬起、好友概要缺失时的匹配误判，并让 HTTP 响应体分块读取严格遵守总时间预算；补齐关机全清帧文件的回归测试。
v0.4.9 补回测试侧 `patch_module_clock` fixture，修复 v0.4.6 总预算用例因 fixture 缺失导致的套件 1 error；产品代码无改动。
v0.4.10 按增量复审修：点击改用授权后实时前台窗口 rect（不再用授权时的旧坐标）；确认码推送只看 `submitted=True`，被拒不再谎报已推送；窗口/前台/画面检查全部通过后才消费授权码；监控线程 `start()` 返回是否真正启动，没起来不报已开启；窗口匹配枚举全部标题命中并先过滤浏览器/终端再取第一个；补 DPI 失败日志、缺 x/y 不再默认点 (0,0)、HTTP 私有 socket 属性取不到时退回读取后检查 deadline；好友候选/时间线/游戏名/商店名统一 `_untrusted` 定界；`plugin.toml` 与 `pyproject.toml` 版本改由测试同步校验。
v0.4.11 修市场验证失败：测试时钟改为只替换被测模块自己的 `time` 名字，不再全局篡改 stdlib `time.monotonic`；`patch_module_clock` fixture 仍保持原有调用方式。
v0.4.12 修复缺失坐标误点、空标题配置不生效、找不到游戏时回退前台窗口、浏览器打开失败误报、聊天响应无上限、Cookie 跨域重定向泄露风险与完整窗口矩形坐标偏差；DPI Awareness 改为首次真实截图/输入时延迟幂等设置。新增 `require_friend_confirm` 与 `game_process`，确认状态机拆分为 `confirmation.py`，入口改为查表分发。
v0.4.13 修复 v0.4.12 发布检查中 DPI 测试直接访问 Windows 专有 `ctypes.windll` 导致的 Linux 失败；Pillow 改为明确使用 N.E.K.O 宿主已提供的运行库，不再让 Ubuntu CI 将 Windows 插件依赖替换为 Linux 二进制，并移除已提交的平台相关 vendor。
v0.4.14 增量深审修复：截图与点击统一到客户区基准（BitBlt 用客户区屏幕原点、PrintWindow 加 `PW_CLIENTONLY`），窗口化游戏不再整体偏移；标题与 `game_process` 都为空时拒绝截图/监控；DPI 设置检查返回值，返回 0 时继续尝试下一个 API 且先设置再取窗口矩形；发送请求已出站但读响应断链归 `SEND_UNCERTAIN`，提示可能已送达、不要直接重试；`note_event` 不再预套 `_untrusted`，避免展示时叠两层定界符。

## 功能

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game` | 按名称或 AppID 启动已安装游戏（`steam.exe -applaunch`） |

### 账号 / 好友 / 商店（v0.3.0）

| 工具 | 说明 | 凭证 |
|---|---|---|
| `steam_account` | 读取账户信息（本地昵称+SteamID；配置 Key 后含在线状态/国家/资料页） | 可选 STEAM_API_KEY |
| `steam_friends` | 好友列表：昵称/SteamID/在线状态，支持昵称过滤 | 需要 STEAM_API_KEY |
| `steam_reply_friend` | 给好友发消息（steamcommunity 网页聊天，最长 500 字） | 需要 STEAM_API_KEY + steam_cookie.txt |
| `steam_game_price` | 查询游戏国区价格（现价/原价/折扣），名称或 AppID | 无需凭证 |
| `steam_promotions` | 查询商店当前特惠/促销游戏 | 无需凭证 |
| `steam_open_app` | 打开任意游戏/应用：已装启动、未装开商店页 | 无需凭证 |

### 游戏助手（v0.2.0）

| 工具 | 说明 |
|---|---|
| `game_capture` | 截取配置中的游戏窗口画面（被遮挡时 PrintWindow 直抓本体），返回窗口/进程/截图路径 |
| `watch_game` | 开启/关闭后台画面监控：周期性截帧，记录行为时间线 |
| `game_state_summary` | 汇总当前局面与近期行为，供 YUI 推理给出合理建议 |
| `take_control` | 玩家显式授权 YUI 接管键鼠（默认关闭，随时交还） |
| `game_action` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 安装

1. 打开 N.E.K.O 客户端 → 插件中心
2. 搜索「Steam管理」，点击安装
3. 本机需已安装 Steam 客户端（常见路径 `C:\Program Files (x86)\Steam`）
4. （可选）账户/好友/回复功能：配置 `STEAM_API_KEY`（环境变量优先），或调用 `steam_credentials`
   手动粘贴 API Key / Cookie；凭证用 Windows DPAPI 加密保存在
   `%LOCALAPPDATA%\N.E.K.O\steam_manager`，不会写进插件源码目录，也不会自动读取浏览器 Cookie
   非 Windows 平台没有 DPAPI，凭证以 `0600` 权限的本地文件保存（无系统级密钥保护）。

## 游戏助手用法

- 用户：**帮我盯着这局游戏** → `watch_game(on=true)` 开启后台监控
- 用户：**现在什么局面，给点建议** → `game_state_summary()` + `game_capture()`，YUI 基于窗口与画面推理建议
- 用户：**你来帮我操作** → 确认画面在场后 `take_control(on=true)` 授权，`game_action` 点击/按键/输入
- 用户：**停止接管** → `take_control(on=false)` 立即交还

## 账号关联说明

- 本插件**不需要、也不会登录你的 Steam 账号**；启动游戏复用本机 Steam 客户端的登录态
- 本地读取：库文件（`libraryfolders.vdf` / `appmanifest_*.acf`）与最近登录账号昵称/SteamID（`config/loginusers.vdf`）
- 在线能力：账户概要/好友列表走 Steam Web API（可选 Key）；回复好友走 steamcommunity 网页聊天（可选 cookie）
- 价格/促销走 Steam Store 公开 API，无需任何凭证
- 未下载到本机的游戏（仅购买未安装）不会出现在本地列表中，但可用 `steam_open_app` 打开商店页

## 安全说明

- 后台监控默认关闭，用户要求才开启；仅保留最近 40 个帧文件（PNG/BMP 混合），且 120 秒内不删，避免删掉视觉通道正在读取的截图
- 键鼠接管默认关闭，必须玩家显式授权；只作用于绑定的游戏窗口且每次操作前复核前台；浏览器/终端/资源管理器进程一律拒绝；找不到标题/进程匹配的游戏窗口时直接拒绝，不回退前台窗口；每步操作记入时间线
- 点击使用真实客户区 `GetClientRect` + `ClientToScreen` 坐标；DPI Awareness 不在 import 时修改，而是在首次真实截图/输入前延迟、幂等设置
- 只读取本地库元数据与屏幕画面；API Key / cookie 用 DPAPI 加密保存、日志与会话输出经过脱敏
- 不自动探测浏览器 Cookie；凭证只能由用户手动粘贴配置，验证通过后才落盘。Cookie 校验/发送请求只在允许的 Valve HTTPS 域名间重定向时保留 Cookie，跨域或降级 HTTP 会移除
- **凭证经过模型的风险属于宿主边界**：手动把 Key/Cookie 发给 YUI 时，凭证会经过宿主模型与对话链路；DPAPI 只保护落盘数据，并未消除这一风险。请仅在可信宿主/模型环境中配置，必要时及时轮换凭证
- 回复好友属于对外写操作，仅在用户明确要求时发送；精确昵称或 17 位 SteamID 匹配，纯数字不按子串猜人
- 昵称仅子串匹配、好友概要部分失败、或无 Key 直发 SteamID 时，插件把 6 位 `confirm_code` 推送到用户界面，用户读出后才能发送；`steam.require_friend_confirm=true` 可强制所有发送（含精确匹配）都确认；未安装游戏打开商店页前同样需要确认码
- 开启键鼠接管前，插件把 6 位 `grant_code` 推送到用户界面；工具结果不返回任何码，模型不能自行授权。接管绑定具体游戏窗口，窗口切走/最小化立即失效
- Steam 旧网页聊天接口的 401/403 诊断为 Cookie 失效，404/405/410 诊断为接口不可用/可能下线；本轮未做带真实凭证的实机聊天验证
- 打包排除：`pyproject.toml` 的 `[tool.neko.build] exclude_files` 明确排除 `steam_api_key.txt`、`steam_cookie.txt`、`*.tmp`
- 不修改任何 Steam 配置

## 依赖与降级

- Win32 窗口、截图与输入调用使用 Python 标准库 `ctypes`；**Pillow（导入名 `PIL`）由 N.E.K.O 宿主提供**，宿主 `pyproject.toml` 已声明 `pillow`，插件不再重复声明私有外部依赖
- Pillow 用于 GDI 像素的 PNG 编码、BMP→PNG 转换，以及超大截图缩放为 JPEG；不可用时 GDI 截图回退为标准库写入 BMP，BMP 转 PNG 失败会保留 BMP
- 插件面向 Windows，而官方发布检查在 Ubuntu 执行；因此不把平台相关的 `vendor/` 提交到仓库，避免 CI sync 将 Windows `.pyd` 替换为 Linux `.so` 后打入发布包
- 超大截图在无法使用 Pillow 缩图时不能生成视觉 `images` 条目，会记录警告；其他本地游戏库、价格等不依赖图像编码的功能不受影响

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `steam.game_window_title` | `civilization vi` | 游戏助手优先查找的窗口标题片段（不区分大小写）；键存在且留空时不做 PrintWindow 直抓，只截取进程匹配的前台窗口 |
| `steam.game_process` | 空 | 可选的窗口进程名精确匹配（不区分大小写），如 `factorio.exe`；切换游戏时建议与标题一并修改 |
| `steam.require_friend_confirm` | `false` | 设为 `true` 时，所有好友发送（含精确昵称/SteamID）都必须经过用户界面确认码 |
| `STEAM_MANAGER_DATA_DIR` | `%LOCALAPPDATA%\N.E.K.O\steam_manager` | 凭证加密存储目录覆盖 |
| `STEAM_API_KEY` | 无 | 环境变量优先于本地配置文件 |

切换到其他游戏的配置示例：

```toml
[steam]
game_window_title = "factorio"
game_process = "factorio.exe"
```

完整注释见 `config.example.toml`。

## 开发与构建

```bash
# 测试（插件已挂载进 N.E.K.O 时）
python -m pytest plugin/plugins/steam_manager/tests/ -q
# 本地克隆（目录名不是 steam_manager）
set NEKO_SOURCE_ROOT=C:\path\to\N.E.K.O
python -m pytest tests -q
# 发布前检查
python -m plugin.neko_plugin_cli.cli check -r steam_manager
# 检查私有外部依赖同步；当前为空，Pillow 由宿主提供
python -m plugin.neko_plugin_cli.cli sync steam_manager --clean
# 构建插件包
python -m plugin.neko_plugin_cli.cli build steam_manager
```

### v0.4.12 本地验证结果

- 在隔离的官方宿主挂载副本中运行完整测试：**147 passed in 2.69s**，其中包含真实 `build_plugin` 打包与凭证排除用例
- `ruff check . --exclude .venv`：**All checks passed**；`git diff --check`：通过
- 官方 `check -r steam_manager`：**check --release passed**，版本 `0.4.12`，测试通过，payload hash 校验通过；隔离挂载副本没有独立 Git 仓库，因此检查器给出对应提示
- 官方 `build steam_manager`：**built=1, failed=0**；另行读取构建包确认两个清单版本均为 `0.4.12`、包含 `vendor/` 中的 Pillow，且不含 API Key、Cookie、`.tmp`、`.git` 或 `.venv` 文件（**该结论已被 v0.4.13 撤销**：`vendor/` 已移除，Pillow 改由 N.E.K.O 宿主提供，发布包不再包含平台相关二进制）
- 未使用真实 Steam 凭证做旧网页聊天实机验证；相关 HTTP 状态码分支由模拟回归测试覆盖

### v0.4.13 发布失败复现与修复验证

- 在 WSL Linux 中对失败提交 `b9ccec6` 实际执行 sync 后，release check 复现为 **1 failed, 145 passed, 1 skipped**：唯一失败是 DPI 测试访问 Linux 不存在的 `ctypes.windll`
- 修复后 Windows 完整测试：**148 passed in 1.59s**；Linux 完整测试：**147 passed, 1 skipped in 0.88s**（既有非 Windows 跳过项）
- 两端官方 sync 均为 **no external dependencies to sync**；官方 `check -r steam_manager` 均通过，版本 `0.4.13`、测试通过、payload hash 校验通过
- 两端官方 `build steam_manager` 均为 **built=1, failed=0**；两个构建包均为 24 个文件，不含私有 vendor、`.pyd`、`.so`、API Key、Cookie、`.tmp`、`.git` 或 `.venv`
- `ruff check . --exclude .venv`：**All checks passed**；`git diff --check`：通过

### v0.4.14 增量深审修复（A694）

- 截图与点击统一到客户区基准：BitBlt 取 `GetClientRect + ClientToScreen` 的屏幕原点，PrintWindow 改用 `PW_CLIENTONLY`；窗口化游戏不再整体偏移，输出尺寸也按客户区计算
- 标题与 `game_process` 都为空时拒绝截图与监控（`WINDOW_TARGET_REQUIRED`），不再把任意前台窗口（含聊天/密码管理器）发给模型
- DPI：`SetProcessDpiAwarenessContext` / `SetProcessDPIAware` 返回 0 不再当成成功，逐个 API 继续尝试；取窗口矩形与查找窗口前先设置，首帧不再按旧缩放换算
- 发送歧义：请求已出站但读响应阶段断链（`TimeoutError` / `ConnectionResetError` / `http.client.HTTPException`）归 `SteamSendUncertain`，输出 `SEND_UNCERTAIN` 并提示可能已送达、不要直接重试
- `note_event` 不再预先套 `_untrusted`，避免 `game_state_summary` 展示时叠出两层定界符
- Windows 完整测试：**158 passed in 8.06s**；`ruff check . --exclude .venv`：**All checks passed**
- 未在真实 Steam 客户端与真实游戏窗口上复跑截图/点击；客户区换算与拒绝分支由离线回归覆盖
