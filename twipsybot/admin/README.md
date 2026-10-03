## 管理命令

### 功能描述

在与机器人聊天页面中使用 `^` 或 `/` 命令管理机器人。

### 使用方法

在 `data/settings.yaml` 的 `bot.admins` 中配置授权用户，或通过 `twipsybot cfg` 修改。

管理本地 Bot 的 `^` 命令：

| 命令 | 说明 |
| --- | --- |
| `^help` | 查看可用命令 |
| `^status` | 机器人状态 |
| `^model` | 查看当前模型 |
| `^model <模型名>` | 切换模型（相同 `openai_base_url`） |
| `^model reset` | 恢复默认模型 |
| `^autopost <rotation\|schedule\|off>` | 切换轮转、定时或关闭自动发帖 |
| `^autopost reset` | 重置当天轮转发帖计数 |
| `^clean posts <天数> [-y]` | 预览或删除超过指定时间且未被互动的帖子 |
| `^reload <插件名>` | 重新读取设置并重载指定插件 |
| `^mention <on\|off>` | 响应提及开关 |
| `^chat <on\|off>` | 响应聊天开关 |
| `^whitelist [list\|add\|del\|set\|clear]` | 查看/修改白名单 |
| `^blacklist [list\|add\|del\|set\|clear]` | 查看/修改黑名单 |

`^model`、`^mention`、`^chat`、`^autopost`、白名单和黑名单都会写入 `data/settings.yaml`。`^reload <插件名>` 重载失败时该插件被禁用，其他插件不受影响。除 `connect` 和全部 `timeline.*` 外，设置变更会热更新。

`^clean posts <天数>` 仅预览，只有追加 `-y` 才会执行不可恢复的删除。Misskey API 每小时最多接受 300 次删除请求，且请求间隔至少 1 秒。命令每次最多处理 300 条。回复、转帖、提及、频道、投票、置顶、Clip 及已有互动的帖子不会删除。

让远程 AI 干活的 `/` 命令：

| 命令 | 说明 | 备注 | 可用场景 |
| --- | --- | --- | --- |
| `/post [-p\|-h\|-f] [-l] <主题>` | 手动发帖 | p/h/f = public/home/followers，l = local_only | 私聊 |
| `/img <描述>` | 生成图片 | 图片保留在机器人账号网盘 | 私聊/群聊/提及 |
