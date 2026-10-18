# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库的 N.E.K.O 插件（Windows），并充当**游戏助手**：读取所玩游戏画面、实时跟踪行为、提供建议，玩家可授权 YUI 接管键鼠代打。

## 功能

### Steam 基础

| 工具 | 说明 |
|---|---|
| `steam_status` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game` | 按名称或 AppID 启动已安装游戏（`steam.exe -applaunch`） |

### 游戏助手（v0.2.0）

| 工具 | 说明 |
|---|---|
| `game_capture` | 截取当前前台游戏窗口画面，返回窗口/进程/截图路径 |
| `watch_game` | 开启/关闭后台画面监控：周期性截帧，记录行为时间线 |
| `game_state_summary` | 汇总当前局面与近期行为，供 YUI 推理给出合理建议 |
| `take_control` | 玩家显式授权 YUI 接管键鼠（默认关闭，随时交还） |
| `game_action` | 接管状态下执行一次键鼠动作（点击/按键/输入文本） |

## 安装

1. 打开 N.E.K.O 客户端 → 插件中心
2. 搜索「Steam管理」，点击安装
3. 本机需已安装 Steam 客户端（常见路径 `C:\Program Files (x86)\Steam`）

## 游戏助手用法

- 用户：**帮我盯着这局游戏** → `watch_game(on=true)` 开启后台监控
- 用户：**现在什么局面，给点建议** → `game_state_summary()` + `game_capture()`，YUI 基于窗口与画面推理建议
- 用户：**你来帮我操作** → 确认画面在场后 `take_control(on=true)` 授权，`game_action` 点击/按键/输入
- 用户：**停止接管** → `take_control(on=false)` 立即交还

## 账号关联说明

- 本插件**不需要、也不会登录你的 Steam 账号**
- Steam 侧只读取本机客户端生成的本地库文件（`libraryfolders.vdf` / `appmanifest_*.acf`）
- 启动游戏复用本机 Steam 客户端的登录态——你只需在 Steam 客户端里保持登录
- 未下载到本机的游戏（仅购买未安装）不会出现在列表中

## 安全说明

- 后台监控默认关闭，用户要求才开启；仅保留最近 20 帧自动清理
- 键鼠接管默认关闭，必须玩家显式授权；仅作用于前台游戏窗口；每步操作记入时间线
- 只读取本地库元数据与屏幕画面，不读取账号、凭据、云存档、聊天等敏感数据
- 不修改任何 Steam 配置
- 全部标准库 + ctypes 实现，无第三方依赖

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

## 开发与构建

```bash
# 测试
python -m pytest plugin/plugins/steam_manager/tests/ -q
# 发布前检查
python -m plugin.neko_plugin_cli.cli check -r steam_manager
```
