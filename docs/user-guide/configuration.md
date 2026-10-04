---
title: 配置
description: 通过 TUI 或 YAML 配置 TwipsyBot 的连接、模型、提示词与生效方式。
---

# 配置

全部配置可在 `twipsybot cfg` 中完成，也可直接编辑 YAML：

| 文件 | 内容 |
| --- | --- |
| `data/secrets.yaml` | `connect`：实例地址、令牌、API 密钥 |
| `data/settings.yaml` | 其余运行设置 |
| `prompts/*.txt` | 可被提示词字段引用 |

字段详见[配置参考](reference/configuration.md)，各功能用法见[功能](features/)。

## TUI

```bash
twipsybot cfg                                  # 本地
docker compose exec twipsybot twipsybot cfg    # Docker
```

| 按键 | 作用 |
| --- | --- |
| `Ctrl+S` | 保存 |
| `Ctrl+R` | 从磁盘重新加载 |
| `Ctrl+Q` | 退出，有未保存修改时确认 |
| `Ctrl+P` | 命令面板，可切换主题 |
| `/` | 聚焦 `logs` 页搜索框 |

- 字段实时校验，无效项标红并阻止保存；状态栏显示未保存、无效与需重启（`↻`）数量。
- 保存只写改动字段并合并到最新文件，不会覆盖管理命令同时写入的变更。
- 列表字段每行一个值；KeyAct `rules`、Topics `rss_list` 原样保存。
- `logs` 页：`lines` 设置显示行数（50–5000，默认 200），`search` 高亮关键词，重复 `Enter` 由新到旧跳转。

## YAML

只需写与默认值不同的字段，未知字段会导致校验失败：

```yaml
bot:
  model: gpt-5.4-mini
autopost:
  rotation: true
reply:
  rate_limit: 30s
```

## 生效方式

运行中约每 2 秒轮询设置文件与引用的提示词文件：

- 有效修改即时生效；无效文件被拒绝并记录错误，保留当前设置。
- `connect` 与 `timeline.*` 需重启，TUI 标记 `↻`，日志提示 `Restart required for: ...`。
- `plugins.<name>` 变更自动重载对应插件，也可发送 `^reload <插件名>`。

## 连接信息

`secrets.yaml` 与设置分离，分享 `settings.yaml` 不会泄露密钥。以下环境变量（或工作目录中的 `.env`）优先于文件，被覆盖字段在 TUI 中标为 `env` 且只读：

`MISSKEY_INSTANCE_URL` · `MISSKEY_ACCESS_TOKEN` · `OPENAI_BASE_URL` · `OPENAI_API_KEY`

## 模型

- `connect.openai_base_url`：OpenAI 兼容地址，默认 DeepSeek，留空为 OpenAI 官方。
- `bot.model`：文本模型 ID，同一端点下可用 `^model <名称>` 热切换。
- `bot.api_mode`：通常保持 `auto`，服务仅支持某一接口时再固定为 `chat` 或 `responses`。

| 功能 | 要求 |
| --- | --- |
| 回复、自动发帖 | `bot.model` |
| `/img` | `bot.image_model` |
| Vision | `bot.model` 支持图片输入 |
| Iincho（默认后端） | 端点支持 `/moderations` |

## 提示词文件

`bot.system_prompt` 与 `autopost.prompt` 可写文本，也可引用 `prompts/` 下的文件，编辑文件同样热更新：

```yaml
bot:
  system_prompt: prompts/system.txt
autopost:
  prompt: prompts/auto-post.txt
```

Docker 示例将宿主机 `./prompts` 只读挂载到 `/app/prompts`。

## 日志

`system.log_level` 日常使用 `INFO`。`DEBUG` 与 `system.dump_events` 仅用于短时排查：原始事件含用户内容，不应长期保留或公开。
