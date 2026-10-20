## 管理命令

在与机器人的聊天中使用，仅限 `bot.admins`。完整说明见[管理命令](https://twipsybot.oreeke.com/user-guide/features/admin-commands)。

| 命令 | 作用 |
| --- | --- |
| `^help` | 命令列表 |
| `^status` | 运行状态 |
| `^model [名称\|reset]` | 查看、切换（同端点）或恢复默认模型 |
| `^autopost rotation\|schedule\|off` | 切换自动发帖模式 |
| `^autopost reset` | 重置当天轮转计数 |
| `^clean posts <天数> [-y]` | 预览 / 删除超期无互动帖子，`-y` 不可恢复 |
| `^mention on\|off` · `^chat on\|off` | 提及 / 聊天开关 |
| `^whitelist` · `^blacklist` | `list`、`add`、`del`、`set`、`clear` |
| `^plugin [list]` · `^plugin on\|off <插件名>` | 插件列表 / 启用、停用 |
| `/post [-p\|-h\|-f] [-l] <主题>` | 私聊中生成并发帖 |

修改写入 `data/settings.yaml`。
