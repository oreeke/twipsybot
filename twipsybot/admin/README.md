## 聊天管理命令

### 功能描述

在聊天中使用 `^` 开头的命令管理机器人<br>

### 使用方法

在 `config.yaml` 的 `bot.admin` 中启用并配置授权用户<br>
在与机器人的聊天页面中使用命令：
| 命令 | 说明 |
| --- | --- |
| `^help` | 可用命令 |
| `^status` | 机器人状态 |
| `^model` | 查看当前模型 |
| `^model <模型名>` | 切换模型 |
| `^model reset` | 恢复默认模型 |
| `^autopost <on\|off>` | 自动发帖开关 |
| `^mention <on\|off>` | 响应提及开关 |
| `^chat <on\|off>` | 响应聊天开关 |
| `^whitelist [list\|add\|del\|set\|clear\|reset]` | 查看/修改白名单 |
| `^blacklist [list\|add\|del\|set\|clear\|reset]` | 查看/修改黑名单 |
