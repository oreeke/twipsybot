---
title: 快速开始
description: 使用 Docker Compose 部署 TwipsyBot，并连接 Misskey 与 OpenAI 兼容模型。
---

# 快速开始

## 准备

- Misskey 独立机器人账号及访问令牌
- OpenAI 兼容服务的 API 密钥
- Docker 与 Docker Compose

令牌按启用功能授权：

| 权限 | 用途 |
| --- | --- |
| `read:account` | 账号、时间线、天线 |
| `write:notes` | 回复、发帖、引用、转帖 |
| `read:chat` · `write:chat` | 读写聊天 |
| `read:drive` · `write:drive` | 读取图片、上传生成图 |
| `write:reactions` | Radar 反应 |

## 部署

```bash
mkdir -p twipsybot/prompts && cd twipsybot
curl -fsSLO https://raw.githubusercontent.com/oreeke/twipsybot/main/docker-compose.yaml
docker compose up -d
docker compose exec twipsybot twipsybot cfg
```

首次启动缺少连接信息，日志提示 `Startup blocked` 并等待。在 `connect` 中填写 `misskey url`（实例根地址）、`misskey token` 与 `openai api key`，`Ctrl+S` 保存，约 2 秒后自动启动。按需修改 compose 中的 `TZ`。

::: tip 模型服务
默认端点为 DeepSeek，`bot.model` 为 `deepseek-flash`。更换服务商时同时修改 `openai base url` 与 `bot.model`；留空 base url 即 OpenAI 官方。
:::

令牌无效或权限不足时持续等待修正，实例不可达时每 60 秒重试。API 密钥与模型不在启动时校验，错误会在首次回复时写入日志：

```bash
docker compose logs -f twipsybot
```

## 验证

1. 用其他账号提及机器人。
2. 管理员私聊发送 `^status`，确认模型与 `mention`、`chat`、`autopost` 开关。

无回复时依次检查令牌权限、`bot.model` 与端点和密钥是否匹配、`reply.mention` / `reply.chat`，以及日志中的鉴权、模型或 Streaming 错误。详见[故障排查](troubleshooting.md)。

## 常用设置

| 字段 | 作用 |
| --- | --- |
| `bot.model` | 文本模型 |
| `bot.admins` | 管理员，可用 `^` 与 `/` 命令 |
| `autopost.rotation` · `autopost.schedule` | 轮转 / 定时发帖 |
| `reply.mention` · `reply.chat` | 提及 / 聊天开关 |

保存即热更新；带 `↻` 的字段（`connect`、`timeline.*`）需执行 `docker compose restart twipsybot`。详见[配置](configuration.md)。

## 本地运行

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/oreeke/twipsybot.git && cd twipsybot
uv sync --python 3.11
uv run twipsybot cfg
uv run twipsybot run
```

### systemd

`/etc/systemd/system/twipsybot.service`：

```ini
[Unit]
Description=TwipsyBot Service
After=network.target

[Service]
Type=exec
WorkingDirectory=/path/to/twipsybot
ExecStart=/path/to/twipsybot/.venv/bin/twipsybot run
KillMode=control-group
TimeoutStopSec=5
Environment=PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now twipsybot
journalctl -u twipsybot -f
```
