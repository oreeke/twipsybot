---
title: Web 联网检索
description: 使用 Web 插件让 AI 在回复前联网检索，并读取消息中的链接。
---

# Web：联网检索

在 AI 回复前联网检索，并抓取消息中的链接，将结果作为参考资料交给模型。仅补充上下文，不接管回复，因此不影响历史、限流与其他插件。

## 配置

```yaml
plugins:
  web:
    enabled: true
    provider: searxng
    endpoint: http://searxng:8080
    always_on: false
    max_results: 5
    fetch_results: false
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `provider` | `searxng` | 检索后端：`searxng` 或 `deepseek` |
| `endpoint` | 空 | 后端地址，`searxng` 必填；`deepseek` 留空使用官方地址 |
| `api_key` | 空 | `deepseek` 必填，与 AI 的密钥相互独立 |
| `always_on` | `false` | 开启后每条消息都先联网检索；关闭则仅 `/web` 触发 |
| `prompt` | 内置 | 附在检索结果前的说明 |
| `empty_prompt` | 内置 | 手动 `/web` 无结果时的提示 |
| `rewrite_prompt` | 内置 | `query_mode: rewrite` 时改写关键词的系统提示词 |
| `language` | `auto` | 检索语言，如 `zh-CN`；留空不指定，仅 `searxng` 生效 |
| `max_results` | `5` | 注入的结果条数，1–10 |
| `fetch_results` | `false` | 读取结果的网页正文；`searxng` 逐条抓取 `max_results` 条结果，`deepseek` 在搜索请求中使用 `need_content` |
| `query_mode` | `raw` | `rewrite` 先让模型改写为搜索关键词，多一次模型调用，推理模型下明显更慢，建议保持 `raw` |
| `min_query` | `4` | `always_on` 下，去掉链接与提及后少于该字数则不检索 |
| `max_links` | `3` | 每条消息读取的链接数，1–10 |
| `max_chars` | `6000` | 注入参考资料的总字符上限 |
| `max_bytes` | `1MB` | 单个网页下载上限，支持 `KB`、`MB` 等单位；`deepseek` 不适用 |
| `timeout` | `10` | 单次请求超时（秒） |
| `cache_ttl` | `600` | 检索与网页缓存秒数，`0` 关闭 |
| `allow_domains` | 空 | 每行一个域名，非空时仅允许这些域名及其子域名 |
| `block_domains` | 空 | 每行一个域名，禁止这些域名及其子域名 |

## 使用

- **手动**：`always_on` 关闭时，在提及、私聊或群聊（需提及）中以 `/web <问题>` 发起，如 `@bot /web 今天有什么科技新闻`。`/web` 前缀不会发给模型。
- **常开**：`always_on` 开启后，每条消息都先检索，无需前缀；`/web` 仍可用。
- **链接**：只要插件启用，消息中的链接（最多 `max_links` 个）都会被抓取，与 `always_on` 无关。链接只识别 ASCII 字符，遇到中文或全角标点即截断；紧跟英文时需用空格分隔。提及时被引用、被回复帖子正文里的链接也会计入，排在本帖链接之后。
- 检索失败或无结果时，手动 `/web` 会让模型如实说明并基于已有知识回答，常开模式则按普通回复处理。
- 参考资料只附加在本次请求中，不写入聊天历史。
- 模型引用来源时，会在回复末尾用编号超链接标注，如 `1 2`，点击编号即可打开对应网页；是否标注由模型决定。

## SearXNG

`provider: searxng` 需启用 JSON 输出，否则会返回 403。在 `settings.yml` 中：

```yaml
search:
  formats:
    - html
    - json
```

仅供机器人内部访问时，建议关闭或放行 `server.limiter`，否则机器人请求可能被限流。示例：

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    volumes:
      - ./searxng:/etc/searxng
```

## DeepSeek

使用 DeepSeek 的 [Search / Fetch 接口](https://api-docs.deepseek.com/zh-cn/guides/search)，搜索与网页抓取均由其服务端完成：

```yaml
plugins:
  web:
    enabled: true
    provider: deepseek
    api_key: sk-xxx
```

- 抓取由 DeepSeek 服务器访问目标网址；`allow_domains` 与 `block_domains` 仍然生效。

## 安全

- 网页抓取仅访问公网地址，重定向同样校验，`endpoint` 不受限。Fake-IP 代理需配置 [`system.allow_nets`](../reference/configuration.md#链接下载安全)。
- 网页内容仅作参考，并要求模型不执行其中的指令，但无法完全杜绝提示注入；公开服务建议配合 `allow_domains`。
- 用户的问题会发送到检索后端及其上游搜索引擎，链接会被机器人所在服务器直接访问；使用 `deepseek` 时，问题与链接都会发送给 DeepSeek，由其服务器访问。
