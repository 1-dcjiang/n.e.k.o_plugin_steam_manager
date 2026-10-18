# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库的 N.E.K.O 插件（Windows）：检测客户端状态、列出已安装游戏、按名称或 AppID 启动游戏。

## 功能

| 工具 | 说明 |
|---|---|
| `steam_status` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game` | 按名称或 AppID 启动已安装游戏（`steam.exe -applaunch`） |

## 安装

1. 打开 N.E.K.O 客户端 → 插件中心
2. 搜索「Steam管理」，点击安装
3. 本机需已安装 Steam 客户端（常见路径 `C:\Program Files (x86)\Steam`）

## 使用示例

- 用户：Steam 开着吗？
- YUI：调用 `steam_status()` → 返回安装路径与运行状态

- 用户：看看我 Steam 里有什么游戏
- YUI：调用 `list_games()` → 返回游戏列表（名称、AppID、占用空间、状态）

- 用户：帮我打开星露谷
- YUI：调用 `launch_game(game="Stardew Valley")` → 通过 Steam 启动该游戏

## 账号关联说明

- 本插件**不需要、也不会登录你的 Steam 账号**
- 它只读取本机 Steam 客户端生成的本地库文件（`libraryfolders.vdf` / `appmanifest_*.acf`）
- 启动游戏复用本机 Steam 客户端的登录态——你只需在 Steam 客户端里保持登录
- 未下载到本机的游戏（仅购买未安装）不会出现在列表中

## 安全说明

- 只读取本地库元数据，不读取账号、凭据、云存档、聊天等敏感数据
- 不修改任何 Steam 配置
- 启动游戏必须来自用户明确指令，且游戏确认已安装

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
