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

打开配置界面：

```bash
twipsybot cfg
```

Docker Compose：

```bash
docker compose exec twipsybot twipsybot cfg
```

运行中的机器人会约每 2 秒热重载 `data/settings.yaml` 和引用的 `prompts/*.txt`。保存无效文件时会被拒绝，当前设置保持不变。

只有 `connect` 和全部 `timeline.*` 修改后需要重启。TUI 会用 `↻` 标记，日志会提示 `Restart required for: ...`：

```bash
docker compose restart twipsybot
```

本地运行则停止当前进程并重新执行 `twipsybot run`。重启前可运行 `twipsybot config-check`。

管理员命令保存的模型、开关、白名单和黑名单会写入 `data/settings.yaml`，重启后继续生效。

## 数据与备份

固定路径：

- 设置：`data/settings.yaml`
- 连接信息：`data/secrets.yaml`
- 数据库：`data/twipsybot.db`
- 日志：`data/logs/twipsybot.log`

SQLite 包含：

- 轮转发帖的日期和每日计数
- 用户回复限制状态
- 插件私有状态，例如 Topics 读取位置和 RSS 历史

备份前应停止机器人，复制 `data/` 及必要的 `prompts/`。`data/secrets.yaml` 含访问令牌与 API 密钥，备份时注意保密。Docker 部署需要从 `twipsybot` 数据卷中备份 `data/`，宿主机上的 `prompts/` 另行备份。

`system.db_clear_days` 只按保留期限清理用户回复限制状态，包括最近回复时间、累计轮数和临时封禁期限；不会清理轮转发帖计数或插件私有数据。手动删除数据库会重置以上全部状态。

## 日志级别

- `INFO`：日常运行。
- `DEBUG`：排查模型、事件和插件行为。
- `WARNING`：只关注可恢复问题。
- `ERROR`：只记录失败。

`system.dump_events` 会输出原始事件，仅应短时用于调试。事件可能包含用户内容，使用后及时关闭，并妥善处理生成的日志。

## 升级

1. 阅读项目的 [`CHANGELOG.md`](https://github.com/oreeke/twipsybot/blob/main/CHANGELOG.md)，确认配置或行为变化。
2. 备份 `data/` 和自定义提示词。
3. 拉取新镜像或源码并同步依赖。
4. 对照最新的 `docker-compose.yaml` 和配置参考。
5. 执行配置检查后启动，并观察首轮连接和插件日志。

新版本新增的设置会自动使用默认值，`data/` 中的现有设置无需手动合并。如果修改过 `docker-compose.yaml`，更新时保留自己的改动。

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

`docker compose down` 不会删除保存设置、数据库和日志的命名卷。不要添加 `--volumes`，除非确认不再需要这些数据。

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
