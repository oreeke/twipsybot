---
title: 快速开始
description: 使用 Docker Compose 快速部署 TwipsyBot，并连接 Misskey 与 OpenAI 兼容模型。
---

# 快速开始

推荐使用 Docker Compose 启动 TwipsyBot。

完成后，机器人可以响应 Misskey 提及和聊天，并按配置自动发帖。

解锁更多玩法，请查看[功能](features/)和[插件](plugins/)。

## 准备账号和密钥

需要准备：

1. Misskey 实例中的独立机器人账号。
2. 该账号的访问令牌。
3. OpenAI 兼容服务的 API 密钥。
4. 已安装 Docker 与 Docker Compose 的主机。

创建 Misskey 访问令牌时，按启用功能授予项目实际使用的权限：

- `read:account`：读取账号、时间线和天线。
- `write:notes`：回复、发帖、引用和转帖。
- `read:chat`：读取聊天消息。
- `write:chat`：发送聊天消息。
- `read:drive`：读取图片及文件信息。
- `write:drive`：上传图片生成结果。
- `write:reactions`：允许 Radar 添加反应。

## 下载配置

直接下载 [`docker-compose.yaml`](https://github.com/oreeke/twipsybot/blob/main/docker-compose.yaml)，或使用终端：

```bash
mkdir -p twipsybot/prompts && cd twipsybot
curl -fsSLO https://raw.githubusercontent.com/oreeke/twipsybot/main/docker-compose.yaml
```

按需调整其中的时区 `TZ`。

## 启动并配置

拉取镜像并启动容器：

```bash
docker compose pull
docker compose up -d
```

首次启动时缺少连接信息，机器人会在日志中提示 `Startup blocked` 并一直等待，容器保持运行。打开配置界面：

```bash
docker compose exec twipsybot twipsybot cfg
```

在 `connect` 中填写以下三项，按 `Ctrl+S` 保存：

- `misskey url`：实例根地址，不要在结尾添加 API 路径。
- `misskey token`：机器人账号的访问令牌。
- `openai api key`：模型服务的 API 密钥。

`openai base url` 默认是 DeepSeek，`bot.model` 默认是 `deepseek-flash`；使用其他服务时一并修改这两项，留空 `openai base url` 表示 OpenAI 官方端点。其余设置可以之后再调。

机器人约 2 秒内检测到保存并自动启动。访问令牌无效或权限不足时会继续等待修正，无法连接实例时每 60 秒重试一次。API 密钥和模型不在启动时校验，填错会在首次回复时出现在日志里。查看启动日志：

```bash
docker compose logs -f twipsybot
```

日志中应能看到账号连接和 Streaming API 建立连接的信息。按 `Ctrl+C` 只会退出日志查看，不会停止容器。

## 调整设置

随时再次运行 `docker compose exec twipsybot twipsybot cfg`。常用修改：

- `bot.model`：文本模型。
- `bot.admins`：可使用 `^` 和 `/` 命令的管理员。
- `autopost.rotation` / `autopost.schedule`：轮转或定时自动发帖。
- `reply.mention` / `reply.chat`：提及和聊天开关。

保存后会自动热更新。TUI 中带 `↻` 的字段（`connect` 和 `timeline.*`）保存后需要重启：

```bash
docker compose restart twipsybot
```

## 验证功能

1. 从另一个账号提及机器人并发送一句简单问题。
2. 使用已授权的账号打开与机器人的聊天，发送 `^status`。
3. 确认回复来自预期模型，并检查状态中的 `mention`、`chat` 和 `autopost` 开关。

如果没有收到回复，先检查：

- 访问令牌是否属于机器人账号且权限充足。
- `bot.model`、`connect` 的 `openai_base_url` 和 API 密钥是否匹配。
- `reply.mention` 或 `reply.chat` 是否开启。
- 日志中是否出现鉴权、模型或 Streaming API 错误。

## 使用本地 Python（可选）

如果喜欢自己配环境或修改源码，适合这种部署方法。

本地运行需要 Python 3.11 或更高版本，使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖：

```bash
git clone https://github.com/oreeke/twipsybot.git
cd twipsybot
uv python install 3.11
uv sync --python 3.11
```

打开配置界面填写 `connect` 和其他设置，然后检查并启动：

```bash
uv run twipsybot cfg
uv run twipsybot config-check
uv run twipsybot run
```

配置保存在 `data/settings.yaml` 与 `data/secrets.yaml`。

## 使用 systemd 托管（可选）

本地部署需要作为后台服务时，可创建 `/etc/systemd/system/twipsybot.service`：

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
Environment=PYTHONUNBUFFERED=1 \
            PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```

将 `/path/to/twipsybot` 替换为实际路径，然后重新加载配置并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl start twipsybot.service
systemctl status twipsybot.service
```

查看实时日志或在更新后重启：

```bash
sudo journalctl -u twipsybot.service -f
sudo systemctl restart twipsybot.service
```

## 安装后检查

- `twipsybot config-check` 或 `uv run twipsybot config-check` 能通过配置检查。
- 进程或容器持续运行，没有反复退出。
- 日志显示 Misskey Streaming API 已建立连接。
- 提及、聊天和 `^status` 能按已启用的配置正常响应。

继续阅读 [配置](configuration.md)，或直接查看 [故障排查](troubleshooting.md)。
