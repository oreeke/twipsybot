<div align="center">

<h1>TwipsyBot</h1>

<br>**一只 Python 实现的 Misskey 机器人**<br><br>
正运行在：[oreeke.com/@ai](https://oreeke.com/@ai)

<a href="https://www.python.org/downloads">
    <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11+-4775b1.svg?style=for-the-badge&labelColor=303030&logo=python&logoColor=4775b1"></a>
<a href="https://github.com/misskey-dev/misskey">
    <img alt="misskey 2025+" src="https://img.shields.io/badge/misskey-2025+-acea31.svg?style=for-the-badge&labelColor=303030&logo=misskey&logoColor=acea31"></a>
<a href="./LICENSE">
    <img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-603669.svg?style=for-the-badge&labelColor=303030&logo=gnu&logoColor=ffffff"></a>

<br>它有时是抽象气氛组，有时是靠谱小帮手。它把所看所想寄向联邦宇宙，也把星尘里的帖子带回家。
<br><br>❤️<br><br>

</div>

## 开始

### 部署方式

#### `a` Docker Compose

- _仅需下载 `docker-compose.yaml.example` 和 `plugins/config.yaml.example` 两个文件_

  ```bash
  mkdir -p twipsybot/plugins && cd twipsybot
  curl -fsSLO https://raw.githubusercontent.com/oreeke/twipsybot/main/docker-compose.yaml.example
  curl -fsSL https://raw.githubusercontent.com/oreeke/twipsybot/main/plugins/config.yaml.example -o plugins/config.yaml.example
  ```

- _复制 `docker-compose.yaml.example` 为 `docker-compose.yaml` 并修改配置_
- _启用插件：复制 `plugins/config.yaml.example` 为 `plugins/config.yaml` 按插件名分节修改_
<details>
<summary><kbd>📃 docker-compose.yaml</kbd></summary>

```yaml
services:
  twipsybot:
    image: ghcr.io/oreeke/twipsybot:latest
    # build: .                                                     # 本地构建
    container_name: twipsybot
    restart: unless-stopped

    volumes:
      - twipsybot:/app/data
      - ./plugins/config.yaml:/app/plugins/config.yaml:ro          # 插件总配置，需提前从 plugins/config.yaml.example 复制创建
      - ./prompts:/app/prompts:ro                                  # 可选，BOT_SYSTEM_PROMPT/BOT_AUTO_POST_PROMPT 引用 prompts/*.txt 时需要

    environment:
      - TZ=Asia/Shanghai                                           # 时区，影响自动发帖计数每日重置等时间相关逻辑
      - MISSKEY_INSTANCE_URL=https://misskey.example.com           # Misskey 实例 URL（本地：http://localhost:port）
      - MISSKEY_ACCESS_TOKEN=your_access_token_here                # Misskey 访问令牌
      - OPENAI_API_KEY=your_api_key_here                           # OpenAI API 密钥
      - OPENAI_MODEL=deepseek-chat                                 # 使用的模型名称
      - OPENAI_API_BASE=https://api.deepseek.com/v1                # OpenAI API 端点
      - OPENAI_API_MODE=auto                                       # auto/chat/responses
      - OPENAI_MAX_TOKENS=1000                                     # 最大生成 token 数
      - OPENAI_TEMPERATURE=0.8                                     # 温度参数
      - BOT_SYSTEM_PROMPT=你是一个可爱的AI助手...                    # 系统提示词（支持文件导入："prompts/*.txt"）
      - BOT_ADMIN_ENABLED=false                                    # 是否启用聊天管理命令
      - BOT_ADMIN_ALLOWED_USERS=admin@example.com                  # 管理员用户 ID 或 username@host
      - BOT_AUTO_POST_ENABLED=true                                 # 是否启用自动发帖
      - BOT_AUTO_POST_INTERVAL=180                                 # 发帖间隔（分钟）
      - BOT_AUTO_POST_MAX_PER_DAY=8                                # 每日最大发帖数量（凌晨 0 点重置计数器）
      - BOT_AUTO_POST_VISIBILITY=public                            # 发帖可见性（public/home/followers）
      - BOT_AUTO_POST_LOCAL_ONLY=false                             # 是否禁用联合（仅本地可见）
      - BOT_AUTO_POST_PROMPT=生成一篇有趣、有见解的社交媒体帖子。      # 发帖提示词
      - BOT_RESPONSE_MENTION=true                                  # 是否响应提及（@）
      - BOT_RESPONSE_CHAT=true                                     # 是否响应聊天
      - BOT_RESPONSE_CHAT_MEMORY=10                                # 聊天上下文记忆长度（条）
      - BOT_RESPONSE_RATE_LIMIT=-1                                 # 回复速率限制：同一用户回复最小间隔；-1 不限制；30s/5m/1h/1d
      - BOT_RESPONSE_RATE_LIMIT_REPLY=我需要休息一下...             # 速率限制回复文案
      - BOT_RESPONSE_MAX_TURNS=-1                                  # 回复次数限制：同一用户最多对话轮数（机器人回复次数）；-1 不限制
      - BOT_RESPONSE_MAX_TURNS_REPLY=我要回家了...                  # 次数限制回复文案
      - BOT_RESPONSE_MAX_TURNS_RELEASE=-1                          # 次数限制解除时间：超限后多久解除；-1 不解除；30s/5m/1h/1d
      - BOT_RESPONSE_WHITELIST=                                    # 白名单：username@host/userId，这些用户不受以上限制
      - BOT_RESPONSE_BLACKLIST=                                    # 黑名单：username@host/userId，这些用户禁止使用回复
      - BOT_TIMELINE_ENABLED=false                                 # 是否订阅时间线（仅用于 Radar）
      - BOT_TIMELINE_ANTENNA_IDS=                                  # antenna ID 或名称（逗号/空格分隔）
      - BOT_TIMELINE_HOME=false                                    # homeTimeline
      - BOT_TIMELINE_LOCAL=false                                   # localTimeline
      - BOT_TIMELINE_HYBRID=false                                  # hybridTimeline
      - BOT_TIMELINE_GLOBAL=false                                  # globalTimeline
      - DB_PATH=data/twipsybot.db                                  # SQLite 路径
      - DB_CLEAR=-1                                                # SQLite 数据保留天数（不含插件）；-1 不清理
      - LOG_PATH=data/logs/twipsybot.log                           # 日志路径
      - LOG_LEVEL=INFO                                             # 日志级别 (DEBUG/INFO/WARNING/ERROR)
      - LOG_DUMP_EVENTS=false                                      # 是否输出事件原始数据（仅用于 DEBUG 数据分析）

volumes:
  twipsybot:
    name: twipsybot
```
</details>

<details>
<summary><kbd>📃 plugins/config.yaml</kbd></summary>

```yaml
keyact:
  enabled: false                                 # 插件开关
  priority: 990                                  # 插件优先级（数字越大越先执行）
  mention_enabled: true                          # 是否处理提及（@）
  chat_enabled: true                             # 是否处理聊天（私信/群聊）
  case_sensitive: false                          # 是否区分大小写
  rules:
    - keywords: ["ping"]                         # 关键词列表
      response: "pong"                           # 回复内容
    - keywords: ["帮助", "help"]
      response: "[帮助文档](https://docs.example.com)"
    - keywords: ["邀请码", "invitation code"]
      response: "1234567890"

radar:
  enabled: false                                 # 插件开关
  priority: 50                                   # 插件优先级（数字越大越先执行）
  reaction: ""                                   # 反应（例如 "heart"/":emoji:"）；留空表示不反应
  reply: false                                   # 是否回复
  reply_text: ""                                 # 自定义回复内容（可用 {username}）；留空可配合 reply_ai
  reply_ai: false                                # AI 自动回复（基于帖子内容生成；reply_text 为空时才生效）
  reply_ai_prompt: ""                            # AI 回复提示词（可用 {content}）；留空使用内置默认提示词
  reply_local_only: false                        # 回复是否禁用联合（仅本地可见）
  renote: false                                  # 是否转发
  renote_visibility: "home"                      # 转发可见性：public/home/followers
  renote_local_only: false                       # 转发是否禁用联合（仅本地可见）
  quote: false                                   # 是否引用
  quote_text: ""                                 # 自定义引用内容（可用 {username}）；留空可配合 quote_ai
  quote_ai: false                                # AI 自动写一句引用感想（quote_text 为空时才生效）
  quote_ai_prompt: ""                            # AI 引用提示词（可用 {content}）；留空使用内置默认提示词
  quote_visibility: "home"                       # 引用可见性：public/home/followers
  quote_local_only: false                        # 引用是否禁用联合（仅本地可见）

topics:
  enabled: false                                 # 插件开关
  priority: 100                                  # 插件优先级（数字越大越先执行）
  source: "txt"                                  # 数据源：txt/rss
  txt_start_line: 1                              # TXT 起始行数，仅在插件数据不存在时生效
  txt_ai_prefix: "以{topic}为主题，"              # TXT 交给 AI 的提示模板，{topic} 为装载的主题
  rss_list:                                      # RSS 订阅列表（source=rss 时生效）
    - "https://example.com/feed.xml"
    - "https://example.com/rss"
  rss_post_mode: "batch"                         # RSS 发送模式：batch=每轮每个 RSS 发一条；rotate=每轮只发一个 RSS 并轮换
  rss_ai: false                                  # RSS 是否交给 AI 处理，RSS 页面至少有标题或摘要，否则需要 AI 有能力通过 URL 预览原文
  rss_ai_prefix: ""                              # RSS 交给 AI 的提示模板；可用 {summary}/{title}/{link}；留空使用默认值

vision:
  enabled: false                                 # 插件开关
  priority: 900                                  # 插件优先级（数字越大越先执行）
  max_images: 1                                  # 单次最多处理图片数量
  max_bytes: 5MB                                 # 单张图片下载大小上限（B/KB/MB）
  use_thumbnail: false                           # 是否使用缩略图（更快但细节更少）
  default_prompt: "请描述图片内容。"               # 用户只发图片不带文字时使用
```
</details>

```bash
docker compose pull
docker compose up -d
```

#### `b` 手动安装

- _克隆仓库_

```bash
git clone https://github.com/oreeke/twipsybot.git
cd twipsybot
```

- _复制 `config.yaml.example` 为 `config.yaml` 并修改配置_
- _启用插件：参考 Docker Compose 部署方式，方法相同_
<details>
<summary><kbd>📃 config.yaml</kbd></summary>

```yaml
misskey:
  instance_url: "https://misskey.example.com"       # Misskey 实例 URL（本地：http://localhost:port）
  access_token: "your_access_token_here"            # Misskey 访问令牌

openai:
  api_key: "your_api_key_here"                      # OpenAI API 密钥
  model: "deepseek-chat"                            # 使用的模型名称
  api_base: "https://api.deepseek.com/v1"           # OpenAI API 端点
  api_mode: "auto"                                  # auto/chat/responses
  max_tokens: 1000                                  # 最大生成 token 数
  temperature: 0.8                                  # 温度参数

bot:
  system_prompt: |                                  # 系统提示词（支持文件导入："prompts/*.txt"）
    你是一个可爱的AI助手，运行在Misskey平台上。
    请用简短、友好的方式发帖和回答问题。

  admin:
    enabled: false                                  # 是否启用聊天管理命令
    allowed_users:
      - "admin@example.com"                         # 用户 ID 或 username@host

  timeline:
    enabled: false                                  # 是否订阅时间线（仅用于 Radar）
    antenna_ids: []                                 # antenna ID 或名称（逗号/空格分隔）
    home: false                                     # homeTimeline
    local: false                                    # localTimeline
    hybrid: false                                   # hybridTimeline
    global: false                                   # globalTimeline

  auto_post:
    enabled: true                                   # 是否启用自动发帖
    interval_minutes: 180                           # 发帖间隔（分钟）
    max_posts_per_day: 8                            # 每日最大发帖数量（凌晨 0 点重置计数器）
    visibility: "public"                            # 发帖可见性（public/home/followers）
    local_only: false                               # 是否禁用联合（仅本地可见）
    prompt: |                                       # 发帖提示词
      生成一篇有趣、有见解的社交媒体帖子。

  response:
    mention: true                                   # 是否响应提及（@）
    chat: true                                      # 是否响应聊天
    chat_memory: 10                                 # 聊天上下文记忆长度（条）
    rate_limit: -1                                  # 回复速率限制：同一用户回复最小间隔；-1 不限制；30s/5m/1h/1d
    rate_limit_reply: "我需要休息一下..."            # 速率限制回复文案
    max_turns: -1                                   # 回复次数限制：同一用户最多对话轮数（机器人回复次数）；-1 不限制
    max_turns_reply: "我要回家了..."                 # 次数限制回复文案
    max_turns_release: -1                           # 次数限制解除时间：超限后多久解除；-1 不解除；30s/5m/1h/1d
    whitelist:                                      # 白名单：username@host/userId，这些用户不受以上限制
      - "admin@example.com"
      - "user-id-123"
    blacklist:                                      # 黑名单：username@host/userId，这些用户禁止使用回复
      - "admin@example.com"
      - "user-id-123"

db:
  path: "data/twipsybot.db"                         # SQLite 路径
  clear: -1                                         # SQLite 数据保留天数（不含插件）；-1 不清理

log:
  path: "data/logs/twipsybot.log"                   # 日志路径
  level: "INFO"                                     # 日志级别 (DEBUG/INFO/WARNING/ERROR)
  dump_events: false                                # 是否输出事件原始数据（仅用于 DEBUG 数据分析）
```
</details>

```bash
pip install -e .

twipsybot up         # 启动
twipsybot status     # 状态
twipsybot down       # 关闭
twipsybot restart    # 重启
twipsybot help       # 帮助

# 或 uv（需安装 uv）
uv sync

uv run twipsybot up
uv run twipsybot ...
```

> _作为服务（可选）_

<details>
<summary><kbd>📃 twipsybot.service</kbd></summary>

```ini
[Unit]
Description=TwipsyBot Service
After=network.target

[Service]
Type=exec
WorkingDirectory=/path/to/twipsybot
ExecStart=/path/to/<venv>/bin/twipsybot up
KillMode=control-group
TimeoutStopSec=5
Environment=TWIPSYBOT_UP_MODE=foreground \
            PYTHONUNBUFFERED=1 \
            PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```
</details>

```bash
systemctl daemon-reload
systemctl start twipsybot.service
```

> [!TIP]
>
> - 虽然自动发帖会尽量绕过 [Prompt caching](https://platform.openai.com/docs/guides/prompt-caching)，但想让内容更丰富请配置并启用 [Topics](./plugins/topics)
> - 切换模型仅需修改 `api_key` `model` `api_base`，相同 `api_base` 的模型可通过聊天管理命令实时切换
> - 使用推理模型时，思维链消耗大，适当增加 `max_tokens` 可以避免 AI 回复中断
> - 机器人使用 [Radar](./plugins/radar) + `antenna` 时间线接收帖子，非必要无需订阅其他时间线（日志噪音大）
> - Docker 部署时，数据库、日志存放于 `twipsybot` 卷，查看可用 `docker compose logs -f twipsybot`

> [!NOTE]
>
> - 请遵守联邦规则，启用机器人账号并在实例内部测试功能，避免设置不当影响其他实例
> - `db.clear` 会重置对用户的回复限制，手动删除数据库文件会丢失聊天管理命令设置的黑白名单

## 生态

### 模型兼容

| 提供商 | 兼容性 | 多模态 |
| :---: | :---: | --- |
| [OpenAI](https://platform.openai.com/docs/quickstart) | ✅ | 📝 👁️ |
| [DeepSeek](https://api-docs.deepseek.com/) | ✅ | 📝 👁️ |
| [xAI](https://docs.x.ai/developers/api-reference) | ✅ | 📝 👁️ |
| [Gemini](https://ai.google.dev/gemini-api/docs/openai) | ✅ | 📝 👁️ |
| [Claude](https://platform.claude.com/docs/en/api/openai-sdk) | ✅ | 📝 👁️ |
| [Ollama](https://ollama.com/blog/openai-compatibility) | ✅ | 📝 👁️ |
| [Perplexity](https://docs.perplexity.ai/docs/agentic-research/openai-compatibility) | ✅ | 📝 👁️ |

### 插件系统

[Plugin API v1 开发指南](./plugins/README.md)

| 插件 | 功能描述 |
| :---: | --- |
| [KeyAct](./plugins/keyact) | 匹配自定义关键词触发回复，绕过 AI |
| [Radar](./plugins/radar) | 与天线推送的帖子互动（反应、回复、转发、引用） |
| [Topics](./plugins/topics) | 为自动发帖提供内容源（文本主题 / RSS） |
| [Vision](./plugins/vision) | 理解 @提及或聊天中的图像并生成回复 |
