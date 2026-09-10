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
3. OpenAI 兼容服务的 API 密钥、API Base 和模型名称。
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

直接下载 [`docker-compose.yaml.example`](https://github.com/oreeke/twipsybot/blob/main/docker-compose.yaml.example) 和 [`plugins/config.yaml.example`](https://github.com/oreeke/twipsybot/blob/main/plugins/config.yaml.example)，或使用终端：

```bash
mkdir -p twipsybot/plugins && cd twipsybot &&
base=https://raw.githubusercontent.com/oreeke/twipsybot/main &&
curl -fsSLO "$base/docker-compose.yaml.example" &&
curl -fsSL "$base/plugins/config.yaml.example" \
  -o plugins/config.yaml.example
```

分别复制为：

```text
docker-compose.yaml
plugins/config.yaml
```

## 填写最小配置

打开 `docker-compose.yaml`，至少修改以下项目：

```yaml
environment:
  - MISSKEY_INSTANCE_URL=https://misskey.example.com
  - MISSKEY_ACCESS_TOKEN=your_access_token_here
  - OPENAI_API_KEY=your_api_key_here
  - OPENAI_MODEL=deepseek-chat
  - OPENAI_API_BASE=https://api.deepseek.com/v1
  - BOT_SYSTEM_PROMPT=你是一个可爱的AI助手...    # 或使用 prompts/*.txt
  - BOT_ADMIN_ALLOWED_USERS=your_username@example.com
```

将实例地址替换为实际地址，不要在结尾添加 API 路径。`BOT_ADMIN_ALLOWED_USERS` 可以填写 Misskey 用户 ID，也可以填写 `username@host`。

如果暂时不希望机器人主动发帖，将以下项目设为 `false`：

```yaml
- BOT_AUTO_POST_ENABLED=false
```

内置插件默认关闭，快速开始阶段无需修改 `plugins/config.yaml`。

## 启动机器人

拉取镜像并启动容器：

```bash
docker compose pull
docker compose up -d
docker compose logs -f twipsybot
```

日志中应能看到配置加载、账号连接和 Streaming API 建立连接的信息。按 `Ctrl+C` 只会退出日志查看，不会停止容器。

## 验证功能

1. 从另一个账号提及机器人并发送一句简单问题。
2. 使用已授权的账号打开与机器人的聊天，发送 `^status`。
3. 确认回复来自预期模型，并检查状态中的 `mention`、`chat` 和 `autopost` 开关。

如果没有收到回复，先检查：

- 访问令牌是否属于机器人账号且权限充足。
- 模型名称、API Base 和 API 密钥是否匹配。
- `BOT_RESPONSE_MENTION` 或 `BOT_RESPONSE_CHAT` 是否开启。
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

准备配置并启动：

```bash
cp config.yaml.example config.yaml
cp plugins/config.yaml.example plugins/config.yaml
uv run twipsybot config-check
uv run twipsybot run
```

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
