# Changelog

## 0.4.0 - 2026-09-01

支持 AI 文生图，还可以随时手动让 AI 发帖。

### Added

- 新增 `/img <描述>` 聊天命令，通过 OpenAI 兼容接口生成图片，上传 Misskey Drive 作为回复附件。
- 新增 `/post <主题>` 聊天命令，复用自动发帖生成与发布参数的手动 AI 发帖，区别是不计入发帖次数。
- 自动发帖每日计数持久化到 SQLite，可用 `^autopost reset` 聊天命令手动重置。

### Changed

- `^` 与 `/` 聊天命令统一使用 `bot.admin.allowed_users` 鉴权，并受现有回复速率和轮次限制。
- `/` 聊天命令由 Admin 统一注册、解析和分发，可独立声明鉴权与私聊限制，并优先于插件处理。
- AI 图片下载限制为 32 MiB，仅接受 PNG、JPEG 和 WebP；生成结果保留在 Misskey Drive 中手动管理。

### Removed

- 移除 `bot.admin.enabled` 与 `BOT_ADMIN_ENABLED`；聊天命令是否可用现在仅由授权列表决定。


## 0.3.0 - 2026-08-30

剥离大量曾因学习计划添加的多余功能和样板，把机器人喂成猪了，本次更新核心代码减重约 `23%`。

### Added

- 新增 `twipsybot config-check`，用于启动前校验配置。
- 新增 `twipsybot version`，用于查看当前安装版本。

### Changed

- 机器人改为前台运行，使用 `twipsybot run` 启动，进程守护交由 Docker 或 systemd。
- 自动发帖间隔改用 `interval`，支持 `30m`、`2h`、`1d` 格式。
- 移除 `timeline.enabled` 总开关；各时间线开关与 `antenna_ids` 现在独立生效。
- 未知配置字段现在会导致启动失败，避免拼写错误被静默忽略。
- 回复限流与解除时间在启动时严格校验，仅接受合法数值、时长或无限制标记。
- 插件布尔配置仅接受布尔值或 `true`、`false` 字符串，避免字符串误判。
- 插件配置仅在启动时读取，修改后需重启机器人生效。
- 插件初始化、启动、关闭、清理、超时、异常隔离及 Hook 排空机制保持有效。
- 聊天管理命令仅保留帮助、状态、模型切换、功能开关及黑白名单管理。
- SQLite 改为单连接串行访问，保留状态持久化、自动清理与关闭后重建。
- 用户级并发锁统一管理，继续保证同一用户请求串行处理。
- 容器直接以前台模式启动；配置或鉴权失败时挂起等待停止信号，避免无限重启。
- 根包公共导出收缩为 `Config` 与 `MisskeyBot`；插件公共 API v1 保持不变。

### Removed

- CLI 的 `up`、`down`、`restart`、`status` 命令及内置守护进程管理。
- 插件运行期启用、禁用与热重载。
- 聊天管理命令：`sysinfo`、`plugins`、`enable`、`disable`、`reload`、`timeline`、`antenna`、`cache`、`cacheclear`、`dbstats`、`dbclear`。
- 自动发帖间隔 `interval_minutes` 字段。
- Misskey Drive 上传、文件管理、目录管理与落盘下载接口。
- OpenAI 结构化输出与 JSON 生成接口。
- 无实际健康诊断能力的容器 PID 健康检查。
- 直接依赖 `psutil` 与 `anyio`。
