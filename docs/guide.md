# Steam管理（steam_manager）

让 YUI 管理本地 Steam 客户端与游戏库（Windows）。

## 工具

| 工具 | 说明 |
|---|---|
| `steam_status()` | 检测 Steam 安装路径与客户端是否运行 |
| `list_games(keyword, limit)` | 列出本地游戏库已安装游戏（名称/AppID/占用空间/状态），支持模糊过滤 |
| `launch_game(game)` | 按名称或 AppID 启动已安装游戏（经 steam.exe -applaunch） |

## 示例

用户说：看看我 Steam 里有什么游戏

YUI 调用 `list_games`，返回：

```
共找到 3 个已安装游戏：
Counter-Strike 2（AppID 730，12003.5 MB，已安装）
DOTA 2（AppID 570，25678.2 MB，已安装）
Stardew Valley（AppID 413150，482.3 MB，已安装）
```

用户说：帮我打开星露谷

YUI 调用 `launch_game(game="Stardew Valley")`，返回：`正在启动「Stardew Valley」（AppID 413150）。`

## 安全边界

- 只读取本地库元数据（libraryfolders.vdf / appmanifest_*.acf）
- 启动游戏必须来自用户明确指令，且游戏确认已安装
- 不读取账号、凭据、云存档、聊天等敏感数据
- 不修改 Steam 配置

## 安装路径识别

1. 注册表 `HKCU\Software\Valve\Steam` → `SteamPath`
2. 兜底：`C:\Program Files (x86)\Steam`、`C:\Program Files\Steam`

未安装 Steam 时，工具会明确提示，不会报错崩溃。
