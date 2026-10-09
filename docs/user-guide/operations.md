---
title: 运维
description: TwipsyBot 状态检查、数据备份与版本升级。
---

# 运维

## 状态

```bash
docker compose ps
docker compose logs -f --tail 200 twipsybot
```

本地运行可用 `twipsybot version`、`twipsybot config-check`。管理员发送 `^status` 可查看账号、模型、运行时长、开关与插件数；TUI 的 `OPS → logs` 可实时查看日志。

配置修改规则见[生效方式](configuration.md#生效方式)。管理命令的修改同样写入 `data/settings.yaml`，重启后保留。

## 数据

| 路径 | 内容 |
| --- | --- |
| `data/settings.yaml` | 一般运行设置 |
| `data/secrets.yaml` | 连接信息与凭据 |
| `data/twipsybot.db` | 轮转发帖计数、回复限制状态、插件私有数据 |
| `data/logs/twipsybot.log` | 日志 |

`system.db_clear_days` 仅清理回复限制状态（最近回复时间、轮数、临时封禁）；删除数据库会重置全部状态。

备份前先停止机器人，复制 `data/` 与 `prompts/`。Docker 下 `data/` 位于 `twipsybot` 命名卷，`prompts/` 在宿主机。`secrets.yaml` 含凭据，注意保密。

## 升级

先阅读 [CHANGELOG](https://github.com/oreeke/twipsybot/blob/main/CHANGELOG.md) 并备份。新增设置自动采用默认值，无需手动合并；已改名或移除的字段不再识别，启动被拦截时在 `twipsybot cfg` 中重新设置并保存，保存时会清除未知字段；若改过 `docker-compose.yaml`，对照最新版本保留自己的改动。

::: code-group

```bash [Docker]
docker compose pull
docker compose up -d
```

```bash [uv]
git pull --ff-only
uv sync
uv run twipsybot config-check
uv run twipsybot run
```

```bash [pip]
git pull --ff-only
pip install -e .
twipsybot config-check
twipsybot run
```

:::

::: warning
`docker compose down` 会保留命名卷；除非确认弃用数据，不要加 `--volumes`。
:::
