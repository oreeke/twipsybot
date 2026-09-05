---
title: 运维
description: 管理 TwipsyBot 日常运行、日志、配置修改、数据备份与版本升级。
---

# 运维

## 日常检查

本地安装：

```bash
twipsybot version
twipsybot config-check
twipsybot run
```

Docker Compose：

```bash
docker compose ps
docker compose logs -f --tail 200 twipsybot
```

机器人启动后，可以由管理员发送 `^status` 查看账号、模型、运行时长、功能开关和插件数量。

## 修改配置

修改 YAML、环境变量或插件配置后需要重启：

```bash
docker compose up -d
```

本地运行则停止当前进程并重新执行 `twipsybot run`。重启前先运行 `twipsybot config-check`。

聊天管理命令保存的模型、白名单和黑名单覆盖会在重启后继续生效。需要回到配置文件值时，使用对应的 `reset` 命令。

## 数据与备份

默认数据库为 `data/twipsybot.db`，默认日志为 `data/logs/twipsybot.log`。数据库包含：

- 自动发帖日期和计数
- 用户回复限制状态
- 管理命令保存的模型和名单覆盖
- 插件私有状态，例如 Topics 读取位置和 RSS 历史

备份前应停止机器人，复制 SQLite 文件及必要的配置和提示词。Docker 部署需要从 `twipsybot` 数据卷中备份。

`db.clear` 只按保留期限清理用户回复限制状态，包括最近回复时间、累计轮数和临时封禁期限；不会清理自动发帖计数、管理命令覆盖或插件私有数据。手动删除数据库则会重置以上全部状态，包括聊天管理命令保存的模型、白名单和黑名单。

## 日志级别

- `INFO`：日常运行。
- `DEBUG`：排查模型、事件和插件行为。
- `WARNING`：只关注可恢复问题。
- `ERROR`：只记录失败。

`log.dump_events` 会输出原始事件，仅应短时用于调试。事件可能包含用户内容，使用后及时关闭，并妥善处理生成的日志。

## 升级

1. 阅读项目的 [`CHANGELOG.md`](https://github.com/oreeke/twipsybot/blob/main/CHANGELOG.md)，确认配置或行为变化。
2. 备份数据库、配置和自定义提示词。
3. 拉取新镜像或源码并同步依赖。
4. 对照最新的 `config.yaml.example` 和 `plugins/config.yaml.example`。
5. 执行配置检查后启动，并观察首轮连接和插件日志。

不要把示例配置直接覆盖到现有配置上。应逐项合并新增字段，并保留自己的令牌和行为设置。

### Docker Compose

更新容器镜像并重新创建服务：

```bash
docker compose pull
docker compose up -d
```

需要停止容器或停止并移除容器时执行：

```bash
docker compose stop
docker compose down
```

`docker compose down` 不会删除保存数据库和日志的命名卷。不要添加 `--volumes`，除非确认不再需要这些数据。

### 本地 Python

停止机器人后拉取源码并同步依赖：

```bash
git pull --ff-only
uv sync
uv run twipsybot config-check
uv run twipsybot run
```

使用 `pip` 安装时执行：

```bash
git pull --ff-only
pip install -e .
twipsybot config-check
twipsybot run
```
