## 聊天管理命令

### 功能描述

在与机器人聊天页面中使用 `^` 或 `/` 命令管理机器人

### 使用方法

在主配置或环境变量 `bot.admin.allowed_users` 中配置授权用户


管理本地 Bot 的 `^` 命令：
| 命令 | 说明 |
| --- | --- |
| `^help` | 可用命令 |
| `^status` | 机器人状态 |
| `^model` | 查看当前模型 |
| `^model <模型名>` | 切换模型 |
| `^model reset` | 恢复默认模型 |
| `^autopost <on\|off\|reset>` | 自动发帖开关或重置每日计数 |
| `^mention <on\|off>` | 响应提及开关 |
| `^chat <on\|off>` | 响应聊天开关 |
| `^whitelist [list\|add\|del\|set\|clear\|reset]` | 查看/修改白名单 |
| `^blacklist [list\|add\|del\|set\|clear\|reset]` | 查看/修改黑名单 |

让远程 AI 干活的 `/` 命令：

| 命令 | 说明 | 可用场景 |
| --- | --- | --- |
| `/post <主题>` | 指示 AI 现在就发一篇帖子 | 私聊 |
| `/img <描述>` | 生成图片 | 私聊/群聊/提及 |
